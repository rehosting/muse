"""The single seam routers call. No parsing/IO logic lives in routers.

When the job/tmux layer arrives it adds methods here (e.g. enqueue_job) and
publishes to the same broker — routers and streaming stay unchanged.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from datetime import datetime

from .. import artifacts, changes, notify, pricing, stats, usage_cache
from ..annotations import AnnotationStore
from ..config import get_settings
from ..models import (
    AlertRules,
    Annotations,
    Bookmark,
    FileChange,
    Investigation,
    InvestigationRef,
    InvestigationSummary,
    Note,
    NotifyConfig,
    NotifyResult,
    SearchHit,
    SearchResponse,
    SessionBacklink,
    SessionEvent,
    SessionLineage,
    SessionSummary,
    StatsResponse,
    SubagentUsage,
    Thread,
    TokenUsage,
    UsageAtAnchor,
    UsagePoint,
    UsageTimeline,
)
from ..ai.digest import DigestResult, build_digest
from ..investigations import InvestigationStore
from ..notify import NotifyStore
from ..paths import find_session
from ..providers import provider_for, providers
from ..search import SearchIndex
from .events import EventBroker
from ..autopilot import tmux
from ..tailer import TailerRegistry
from ..file_index import FileIndex
from ..patterns import HealthStore, detect_patterns
from ..packs import PackStore
from ..usage_history import UsageHistoryStore
from ..worklog import WorklogStore

# Short TTLs so repeated polls (multiple tabs/pollers) reuse one computation
# instead of each re-scanning every transcript. The underlying scanners are
# already mtime-incremental; these caps stop the per-request *recompute +
# aggregation* from pinning a core under polling. Monitoring data tolerates a
# few seconds of staleness, and live updates still arrive via SSE.
# Sessions are served stale-while-revalidate: a request returns the cached
# snapshot instantly and a background thread refreshes it past this age, so the
# list endpoint never blocks on a filesystem scan (which competes for the GIL
# with the search-index watcher). The cache is also kept warm by the alerts tick.
_SESSIONS_TTL = 15.0
_STATS_TTL = 15.0
_SEARCH_REFRESH_TTL = 30.0  # on-demand fallback if the watcher isn't refreshing


class SessionService:
    def __init__(self, broker: EventBroker) -> None:
        self.broker = broker
        self.tailers = TailerRegistry(broker)
        self.store = AnnotationStore(get_settings().db_path)
        self.search_index = SearchIndex(get_settings().db_path)
        self.notify_store = NotifyStore(get_settings().db_path)
        self.investigations = InvestigationStore(get_settings().db_path)
        self.worklog = WorklogStore(get_settings().db_path)
        self.file_index = FileIndex(get_settings().db_path)
        self.health = HealthStore(get_settings().db_path)
        self.usage_history = UsageHistoryStore(get_settings().db_path)
        self.packs = PackStore(
            get_settings().db_path, get_settings().db_path.parent / "packs"
        )
        self._sessions_cache: Optional[list[SessionSummary]] = None
        self._sessions_ts = 0.0
        self._sessions_lock = threading.Lock()
        self._sessions_refreshing = False
        self._stats_caches: dict[int, tuple[float, StatsResponse]] = {}
        self._search_refresh_ts = 0.0
        self._brief_cache: dict[str, tuple[float, dict]] = {}
        # Parse caches: the viewer page bursts 6–8 requests that all need the
        # same parsed transcript (thread, events, file changes, brief, health
        # sync). These mtime-keyed LRUs collapse that burst — and the background
        # index ticks — to one parse per artifact. Entries are validated by
        # (mtime, size) of the backing file, so a live append invalidates
        # immediately. Oversized transcripts are served but never cached.
        self._thread_cache: OrderedDict = OrderedDict()
        self._events_cache: OrderedDict = OrderedDict()
        self._parse_lock = threading.Lock()

    # --- read APIs -----------------------------------------------------------
    def list_sessions(self) -> list[SessionSummary]:
        """Stale-while-revalidate: return the cached snapshot immediately and
        refresh in the background once it ages past the TTL. Only the very first
        call (cold, no cache) builds synchronously. This keeps the list endpoint
        off the filesystem-scan path so it can't be starved by background work
        (the search-index watcher) competing for the GIL."""
        cache = self._sessions_cache
        if cache is not None:
            if (time.monotonic() - self._sessions_ts) >= _SESSIONS_TTL:
                self._kick_sessions_refresh()  # non-blocking
            return cache
        return self._rebuild_sessions()  # cold: one-time synchronous build

    def _kick_sessions_refresh(self) -> None:
        with self._sessions_lock:
            if self._sessions_refreshing:
                return
            self._sessions_refreshing = True
        threading.Thread(target=self._bg_rebuild_sessions, daemon=True).start()

    def _bg_rebuild_sessions(self) -> None:
        try:
            self._rebuild_sessions()
        except Exception:
            pass
        finally:
            self._sessions_refreshing = False

    def _rebuild_sessions(self) -> list[SessionSummary]:
        summaries: list[SessionSummary] = []
        for prov in providers():
            try:
                summaries.extend(prov.iter_sessions())
            except Exception:
                pass  # one provider failing shouldn't blank the whole list
        titles = self.store.all_titles()  # custom renames override derived titles
        health = self.health.scores()  # one table read; snapshots refresh in the tick
        for s in summaries:
            custom = titles.get(s.session_id)
            if custom:
                s.title = custom
                s.title_source = "custom"
            s.health = health.get(s.session_id)
        summaries.sort(key=lambda s: s.mtime, reverse=True)
        self._sessions_cache = summaries
        self._sessions_ts = time.monotonic()
        return summaries

    def get_stats(self, days: int = 0) -> StatsResponse:
        cached = self._stats_caches.get(days)
        if cached is not None and (time.monotonic() - cached[0]) < _STATS_TTL:
            return cached[1]
        result = stats.compute_stats(days, self.usage_history)
        self._stats_caches[days] = (time.monotonic(), result)
        return result

    def roll_usage_history(self) -> int:
        """Warm the usage cache AND persist per-day rollups (called by the
        alerts tick) so long-range stats survive transcript deletion."""
        return self.usage_history.roll(usage_cache.scan_all().events)

    # --- parse caches ---------------------------------------------------------
    _PARSE_CACHE_SLOTS = 6
    _CACHE_MAX_BYTES = 300 * 1024 * 1024  # never hold a multi-GB session in RAM

    def _change_key(self, session_id: str, agent_id: Optional[str]) -> Optional[tuple]:
        """Cheap freshness key for a session's backing file: (mtime, size).
        Claude stats the transcript directly; other providers fall back to the
        cached summary (their files are small and list_sessions is SWR-fresh)."""
        if ":" not in session_id:
            paths = find_session(session_id)
            if paths is None:
                return None
            f = paths.subagent_jsonl(agent_id) if agent_id else paths.jsonl
            try:
                st = f.stat()
                return (st.st_mtime, st.st_size)
            except OSError:
                return None
        s = next(
            (x for x in (self._sessions_cache or []) if x.session_id == session_id),
            None,
        )
        return (s.mtime.timestamp(), s.size_bytes) if s else None

    def _parse_cached(
        self,
        cache: OrderedDict,
        session_id: str,
        agent_id: Optional[str],
        build: Callable,
    ):
        key = (session_id, agent_id)
        ck = self._change_key(session_id, agent_id)
        with self._parse_lock:
            hit = cache.get(key)
            if hit is not None and ck is not None and hit[0] == ck:
                cache.move_to_end(key)
                return hit[1]
        value = build()  # parse OUTSIDE the lock (don't serialize big parses)
        if value is not None and ck is not None and ck[1] <= self._CACHE_MAX_BYTES:
            with self._parse_lock:
                cache[key] = (ck, value)
                cache.move_to_end(key)
                while len(cache) > self._PARSE_CACHE_SLOTS:
                    cache.popitem(last=False)
        return value

    def get_thread(self, session_id: str) -> Optional[Thread]:
        thread = self._parse_cached(
            self._thread_cache, session_id, None,
            lambda: provider_for(session_id).load_thread(session_id),
        )
        if thread:
            # Applied on every call (not cached) so renames take effect instantly.
            custom = self.store.get_title(session_id)
            if custom:
                thread.title = custom
                thread.title_source = "custom"
        return thread

    # --- notifications (outbound push via ntfy) -----------------------------
    def get_notify_config(self) -> NotifyConfig:
        return self.notify_store.get_config()

    def set_notify_config(self, cfg: NotifyConfig) -> NotifyConfig:
        return self.notify_store.set_config(cfg)

    def get_alert_rules(self) -> AlertRules:
        return self.notify_store.get_rules()

    def set_alert_rules(self, rules: AlertRules) -> AlertRules:
        return self.notify_store.set_rules(rules)

    def send_notification(
        self, message: str, *, title: str | None = None, click: str | None = None,
        tags: str | None = None, priority: int | None = None, config: NotifyConfig | None = None,
    ) -> NotifyResult:
        """Send a push using the stored (or supplied) config. The seam the rules
        engine will call when a session errors/finishes/awaits the user."""
        cfg = config or self.notify_store.get_config()
        return notify.send(
            cfg, message, title=title, click=click, tags=tags, priority=priority
        )

    # --- annotations (writable; never touches ~/.claude) --------------------
    def get_annotations(self, session_id: str) -> Annotations:
        return self.store.get_annotations(session_id)

    def set_title(self, session_id: str, title: Optional[str]) -> Annotations:
        self.store.set_title(session_id, title)
        return self.store.get_annotations(session_id)

    def upsert_bookmark(self, session_id: str, message_uuid: str, note: str) -> Bookmark:
        return self.store.upsert_bookmark(session_id, message_uuid, note)

    def delete_bookmark(self, session_id: str, message_uuid: str) -> None:
        self.store.delete_bookmark(session_id, message_uuid)

    def get_subagent(self, session_id: str, agent_id: str) -> Optional[Thread]:
        return self._parse_cached(
            self._thread_cache, session_id, agent_id,
            lambda: provider_for(session_id).load_subagent(session_id, agent_id),
        )

    # --- cross-session search -----------------------------------------------
    def refresh_search_index(self) -> None:
        """Bring the FTS index up to date across all providers. Called off the
        request path by the AlertsWatcher tick so a keystroke-search stays a fast
        index read (only files whose mtime changed are re-indexed)."""
        docs = []
        for prov in providers():
            try:
                docs.extend(prov.search_docs())
            except Exception:
                pass
        self.search_index.sync(docs)
        self._search_refresh_ts = time.monotonic()

    def refresh_file_index(self) -> int:
        """Bring the file-activity index up to date: only sessions whose mtime
        advanced are re-extracted (rate-limited per session inside sync), so a
        live transcript isn't re-parsed every tick. Called by the alerts tick."""
        def _changes(session_id: str):
            try:
                return self.get_file_changes(session_id)
            except Exception:
                return None
        return self.file_index.sync(self._indexable_sessions(), _changes)

    def _indexable_sessions(self) -> list[SessionSummary]:
        """Sessions the background indexes may re-parse. Pathologically large
        transcripts (multi-GB) are excluded — a tick must never spend minutes
        pegging a core on one file (no badge/file-activity for those)."""
        return [
            s for s in self.list_sessions() if s.size_bytes <= self._CACHE_MAX_BYTES
        ]

    def refresh_health_index(self) -> int:
        """Re-score failure patterns for sessions whose mtime advanced (rate-
        limited per session inside sync). Called by the alerts tick."""
        def _events(session_id: str):
            try:
                return self.get_events(session_id)
            except Exception:
                return None
        return self.health.sync(self._indexable_sessions(), _events)

    def get_session_health(self, session_id: str, fresh: bool = False) -> Optional[dict]:
        """Failure patterns for one session. Default: serve the snapshot (one
        table read — the tick keeps it within ~5 min of live, plenty for a badge
        bar). `fresh=True` recomputes from the (cached) events for MCP callers
        that are about to cite the patterns."""
        if not fresh:
            snap = self.health.get(session_id)
            if snap is not None:
                return snap
        events = self.get_events(session_id)
        if events is not None:
            return detect_patterns(events)
        return self.health.get(session_id)

    # --- file activity (cross-session: who touched this file?) ----------------
    def search_files(self, q: str, limit: int = 50) -> list[dict]:
        return self.file_index.search_files(q, limit)

    def file_activity(self, file_path: str) -> list[dict]:
        groups = self.file_index.activity_for(file_path)
        titles = {s.session_id: s.title for s in self.list_sessions()}
        for g in groups:
            g["title"] = titles.get(g["session_id"])
        return groups

    def get_related_sessions(self, session_id: str, limit: int = 5) -> list[dict]:
        """Deterministic related-session scoring: same project (+2), edited-file
        Jaccard overlap (+0–5), same starting git branch (+2), temporal adjacency
        within 24h (+1). Shared files / branch are returned as the explanation."""
        me = next((s for s in self.list_sessions() if s.session_id == session_id), None)
        mine = self.file_index.edited_files(session_id)
        sharing = {
            d["session_id"]: d["shared_files"]
            for d in self.file_index.sessions_sharing_files(session_id)
        }
        scored = []
        for s in self.list_sessions():
            if s.session_id == session_id:
                continue
            score = 0.0
            shared = sharing.get(s.session_id, [])
            same_branch = bool(
                me and me.git_branch and s.git_branch == me.git_branch
            )
            if me and s.project_cwd and s.project_cwd == me.project_cwd:
                score += 2
            if shared and mine:
                theirs = self.file_index.edited_files(s.session_id)
                union = len(mine | theirs)
                if union:
                    score += 5 * len(set(shared) & mine) / union
            if same_branch:
                score += 2  # started on the same branch (it may drift mid-session)
            if me and abs(s.mtime.timestamp() - me.mtime.timestamp()) < 86400:
                score += 1
            if score >= 2:  # same-project alone qualifies; mere adjacency doesn't
                scored.append({
                    "summary": s, "score": round(score, 2), "shared_files": shared,
                    "same_branch": same_branch,
                })
        scored.sort(key=lambda d: d["score"], reverse=True)
        return scored[:limit]

    def search(self, q: str, limit: int = 30) -> SearchResponse:
        # The watcher keeps the index warm; only refresh inline if it's gone
        # stale (e.g. watcher disabled), and at most once per TTL.
        if (time.monotonic() - self._search_refresh_ts) > _SEARCH_REFRESH_TTL:
            self.refresh_search_index()
        rows, loose = self.search_index.search(q, limit)
        # Enrich with titles/cwd from the (TTL-cached) session list.
        summaries = {s.session_id: s for s in self.list_sessions()}
        hits: list[SearchHit] = []
        for r in rows:
            summ = summaries.get(r["session_id"])
            ts = None
            if r.get("ts"):
                try:
                    ts = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
                except ValueError:
                    ts = None
            hits.append(
                SearchHit(
                    session_id=r["session_id"],
                    project_cwd=summ.project_cwd if summ else None,
                    title=summ.title if summ else r["session_id"],
                    uuid=r.get("uuid"),
                    role=r.get("role"),
                    timestamp=ts,
                    snippet=r.get("snippet") or "",
                )
            )
        return SearchResponse(
            query=q,
            indexed_sessions=self.search_index.indexed_sessions(),
            available=self.search_index.available,
            loose=loose,
            hits=hits,
        )

    def get_events(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[SessionEvent]]:
        return self._parse_cached(
            self._events_cache, session_id, agent_id,
            lambda: provider_for(session_id).build_events(session_id, agent_id),
        )

    def get_file_changes(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[FileChange]]:
        # Claude main threads: derive from the (cached) thread instead of letting
        # the provider re-parse the transcript a second time.
        if ":" not in session_id and agent_id is None:
            thread = self.get_thread(session_id)
            return changes.build_file_changes(thread) if thread else None
        return provider_for(session_id).build_file_changes(session_id, agent_id)

    def get_lineage(self, session_id: str) -> Optional[SessionLineage]:
        return provider_for(session_id).build_lineage(session_id)

    # --- session navigation (incremental, so big sessions never need disk) ----
    _STEP_CAP = 50000  # one expanded step
    _PAGE_STEP_CAP = 1500  # per-step text when paging
    _COMPACT_PREVIEW = 2000
    # Max chars in a paginated nav result's JSON, so it stays well under an MCP
    # client's per-tool-result token cap (~25k tokens) and never spills to disk.
    # ~28k chars ≈ 11k tokens even for the uuid-dense rows here.
    _MCP_RESULT_CHARS = 28000

    def _pack_rows(self, rows: list[dict], offset: int) -> tuple[list[dict], Optional[int]]:
        """Take rows[offset:] up to the MCP char budget. Returns (kept, next_offset)
        where next_offset is the index to resume from, or None when exhausted — so a
        big skeleton/error list pages cleanly instead of overflowing the transport."""
        kept: list[dict] = []
        chars = 0
        i = max(0, offset)
        while i < len(rows):
            c = len(json.dumps(rows[i], default=str)) + 1
            if kept and chars + c > self._MCP_RESULT_CHARS:
                break
            kept.append(rows[i])
            chars += c
            i += 1
        return kept, (i if i < len(rows) else None)

    def _tool_index(self, session_id: str) -> dict:
        """tool_use_id -> ToolUse (full input + result) for the main thread."""
        thread = self.get_thread(session_id)
        idx: dict = {}
        if thread:
            for it in thread.items:
                for b in it.blocks:
                    if b.tool_use:
                        idx[b.tool_use.id] = b.tool_use
        return idx

    def _tool_result_text(self, session_id: str, tu, cap: int) -> str:
        if not tu or not tu.result:
            return ""
        content = tu.result.content or ""
        if tu.result.truncated and tu.result.cache_id:
            full = self.get_persisted_output(session_id, tu.result.cache_id, 0, cap)
            if full and full.get("content"):
                content = full["content"]
        return content[:cap]

    def get_session_outline(self, session_id: str, offset: int = 0) -> Optional[dict]:
        """Cheap skeleton of a session: user turns, subagent spawns, compaction
        boundaries, and errors — each with its anchor. The story of a big session
        without the bulk; drill in with get_step. Paginated by a char budget so even
        a huge spine returns inline — pass the returned next_offset for more."""
        events = self.get_events(session_id)
        if events is None:
            return None
        rows = []
        for e in events:
            if e.is_compaction:
                kind = "compaction"
            elif e.is_error or (e.kind == "system" and e.level == "error"):
                kind = "error"
            elif e.kind in ("user", "subagent"):
                kind = e.kind
            else:
                continue
            rows.append({
                "anchor": e.anchor_uuid or e.tool_use_id,
                "kind": kind,
                "ts": e.timestamp.isoformat() if e.timestamp else None,
                "label": (e.label or "")[:160],
            })
        kept, next_offset = self._pack_rows(rows, offset)
        return {"session_id": session_id, "step_count": len(events),
                "outline_count": len(rows), "offset": max(0, offset), "outline": kept,
                "next_offset": next_offset, "truncated": next_offset is not None}

    def get_step(self, session_id: str, anchor_uuid: str) -> Optional[dict]:
        """Expand exactly one step to its FULL content (untruncated message/thinking/
        system text, or a tool's full input + result). The thing the digest can't
        give you."""
        events = self.get_events(session_id)
        if events is None:
            return None
        matches = [e for e in events if anchor_uuid in (e.anchor_uuid, e.tool_use_id)]
        if not matches:
            return {"session_id": session_id, "anchor_uuid": anchor_uuid, "found": False}
        idx = None
        out = []
        for e in matches:
            row = {
                "kind": e.kind, "type": e.type, "role": e.role,
                "ts": e.timestamp.isoformat() if e.timestamp else None,
                "tool_name": e.tool_name, "tool_use_id": e.tool_use_id,
                "is_error": e.is_error, "label": e.label, "detail": e.detail,
            }
            if e.kind in ("tool_call", "tool_result", "subagent") or e.tool_use_id:
                if idx is None:
                    idx = self._tool_index(session_id)
                tu = idx.get(e.tool_use_id or "")
                if tu:
                    row["tool_input"] = tu.input
                    res = self._tool_result_text(session_id, tu, self._STEP_CAP)
                    if res:
                        row["tool_result"] = res
            out.append(row)
        return {"session_id": session_id, "anchor_uuid": anchor_uuid, "found": True, "steps": out}

    def get_errors(self, session_id: str, offset: int = 0) -> Optional[dict]:
        """Every error in a session (tool errors + system/API errors) with full text
        and anchors — first-class, not a '… and 80 more' tail. Paginated by a char
        budget (errors carry full detail) so it returns inline; page with next_offset."""
        events = self.get_events(session_id)
        if events is None:
            return None
        errs = [e for e in events if e.is_error or (e.kind == "system" and e.level == "error")]
        idx = None
        out = []
        for e in errs:
            detail = e.detail
            if not detail and e.kind == "tool_result":
                if idx is None:
                    idx = self._tool_index(session_id)
                detail = self._tool_result_text(session_id, idx.get(e.tool_use_id or ""), 8000)
            out.append({
                "anchor": e.anchor_uuid or e.tool_use_id, "kind": e.kind,
                "tool_name": e.tool_name,
                "ts": e.timestamp.isoformat() if e.timestamp else None,
                "label": e.label, "detail": detail,
            })
        kept, next_offset = self._pack_rows(out, offset)
        return {"session_id": session_id, "error_count": len(errs),
                "offset": max(0, offset), "errors": kept,
                "next_offset": next_offset, "truncated": next_offset is not None}

    def get_compactions(self, session_id: str) -> Optional[dict]:
        """Compaction boundaries with their summary text. Returns a preview per
        summary + the summary's anchor; call get_step(summary_anchor) for the full
        (often multi-thousand-word) summary."""
        events = self.get_events(session_id)
        if events is None:
            return None
        out = []
        for i, e in enumerate(events):
            if not e.is_compaction:
                continue
            summary = summary_anchor = None
            for f in events[i + 1: i + 6]:  # Claude: isCompactSummary user msg follows boundary
                if f.kind == "user" and f.detail:
                    summary, summary_anchor = f.detail, f.anchor_uuid
                    break
            if summary is None and e.detail:  # Codex et al.: summary is on the boundary itself
                summary, summary_anchor = e.detail, e.anchor_uuid
            out.append({
                "anchor": e.anchor_uuid,
                "ts": e.timestamp.isoformat() if e.timestamp else None,
                "label": e.label, "meta": e.detail,
                "summary_anchor": summary_anchor,
                "summary_preview": (summary or "")[: self._COMPACT_PREVIEW],
                "summary_truncated": bool(summary and len(summary) > self._COMPACT_PREVIEW),
            })
        return {"session_id": session_id, "count": len(out), "compactions": out}

    def get_session_steps(
        self, session_id: str, offset: int = 0, limit: int = 40,
        kinds: Optional[list[str]] = None,
    ) -> Optional[dict]:
        """Page through a session's steps in order. `kinds` filters (event kinds plus
        'error'/'compaction'). Returns a bounded window + next_offset — never spills."""
        events = self.get_events(session_id)
        if events is None:
            return None
        limit = max(1, min(limit, 80))
        if kinds:
            kset = set(kinds)
            sel = [
                e for e in events
                if e.kind in kset
                or ("error" in kset and e.is_error)
                or ("compaction" in kset and e.is_compaction)
            ]
        else:
            sel = events
        window = sel[offset: offset + limit]
        rows = []
        for j, e in enumerate(window):
            text = (e.detail or e.label or "")
            rows.append({
                "index": offset + j,
                "anchor": e.anchor_uuid or e.tool_use_id,
                "kind": "compaction" if e.is_compaction else e.kind,
                "ts": e.timestamp.isoformat() if e.timestamp else None,
                "is_error": e.is_error,
                "text": text[: self._PAGE_STEP_CAP],
                "more": len(text) > self._PAGE_STEP_CAP,  # use get_step(anchor) for full
            })
        nxt = offset + limit
        return {"session_id": session_id, "total": len(sel), "offset": offset,
                "limit": limit, "next_offset": nxt if nxt < len(sel) else None, "steps": rows}

    def _claude_usage_events(self, session_id: str) -> list:
        """All usage events (main + subagents) for a Claude session id."""
        return [e for e in usage_cache.scan_all().events if e.sid == session_id]

    @staticmethod
    def _event_cost(e) -> float:
        return pricing.cost_usd(e.model, e.input, e.output, e.cc, e.cr)

    def get_session_tokens(self, session_id: str) -> Optional[TokenUsage]:
        """Per-session token usage + authoritative cost. For Claude, sums every usage
        event for this session id — main thread AND all subagents (the usage cache
        rolls subagent files onto the parent id) — with a real/cache split, a
        per-subagent breakdown, and muse-computed cost (pricing.py, per-event model).
        Other providers return the flat token total from their summary."""
        prov = provider_for(session_id)
        if prov.id == "claude":
            events = self._claude_usage_events(session_id)
            if not events and not any(
                s.session_id == session_id for s in self.list_sessions()
            ):
                return None
            inp = sum(e.input for e in events)
            out = sum(e.output for e in events)
            cc = sum(e.cc for e in events)
            cr = sum(e.cr for e in events)
            real = inp + out + cc
            cost = sum(self._event_cost(e) for e in events)
            sub_events = [e for e in events if e.is_subagent]
            sub_real = sum(e.input + e.output + e.cc for e in sub_events)
            sub_cost = sum(self._event_cost(e) for e in sub_events)
            subagents = self._subagent_usage(sub_events)
            models = sorted({e.model for e in events if e.model and not e.model.startswith("<")})
            return TokenUsage(
                session_id=session_id, provider="claude",
                input_tokens=inp, output_tokens=out,
                cache_creation_tokens=cc, cache_read_tokens=cr,
                total_tokens=real, total_with_cache_read=real + cr,
                main_tokens=real - sub_real, subagent_tokens=sub_real,
                subagent_count=len(subagents),
                cost_usd=round(cost, 4), main_cost_usd=round(cost - sub_cost, 4),
                subagent_cost_usd=round(sub_cost, 4),
                models=models, subagents=subagents,
            )
        # Non-Claude: flat token total from the (mtime-cached) summary.
        summ = next((s for s in self.list_sessions() if s.session_id == session_id), None)
        if summ is None:
            return None
        return TokenUsage(
            session_id=session_id, provider=summ.provider,
            total_tokens=summ.total_tokens, total_with_cache_read=summ.total_tokens,
            main_tokens=summ.total_tokens, breakdown_available=False,
        )

    def _subagent_usage(self, sub_events: list) -> list[SubagentUsage]:
        by_agent: dict[str, dict] = {}
        for e in sub_events:
            a = by_agent.setdefault(
                e.agent_id, {"type": e.agent_type, "in": 0, "out": 0, "cc": 0, "cr": 0, "cost": 0.0}
            )
            a["in"] += e.input
            a["out"] += e.output
            a["cc"] += e.cc
            a["cr"] += e.cr
            a["cost"] += self._event_cost(e)
        out = [
            SubagentUsage(
                agent_id=aid, agent_type=a["type"],
                input_tokens=a["in"], output_tokens=a["out"],
                cache_creation_tokens=a["cc"], cache_read_tokens=a["cr"],
                total_tokens=a["in"] + a["out"] + a["cc"], cost_usd=round(a["cost"], 4),
            )
            for aid, a in by_agent.items()
        ]
        return sorted(out, key=lambda s: s.cost_usd, reverse=True)

    def get_usage_at_anchor(self, session_id: str, anchor_uuid: str) -> Optional[UsageAtAnchor]:
        """Cumulative spend (tokens + cost) up to and including a given step — the
        'cost to reach milestone X' answer. Resolves the anchor's timestamp from the
        session timeline, then sums all usage events (main + subagents) at or before
        it. Claude-only (others lack per-step usage)."""
        if provider_for(session_id).id != "claude":
            return None
        events_tl = self.get_events(session_id) or []
        cut = next((e for e in events_tl if e.anchor_uuid == anchor_uuid), None)
        if cut is None or cut.timestamp is None:
            return UsageAtAnchor(session_id=session_id, anchor_uuid=anchor_uuid, found=False)
        cut_ts = cut.timestamp
        counted = [
            e for e in self._claude_usage_events(session_id) if e.ts and e.ts <= cut_ts
        ]
        tokens = sum(e.input + e.output + e.cc for e in counted)
        cost = sum(self._event_cost(e) for e in counted)
        return UsageAtAnchor(
            session_id=session_id, anchor_uuid=anchor_uuid, found=True,
            cutoff_timestamp=cut_ts, cumulative_tokens=tokens,
            cumulative_cost_usd=round(cost, 4), event_count=len(counted),
        )

    def get_usage_timeline(self, session_id: str, limit: int = 100) -> Optional[UsageTimeline]:
        """Cost-over-time: cumulative tokens + cost at each user turn (a natural
        milestone boundary). Sampled to `limit` points for long sessions. Claude-only."""
        if provider_for(session_id).id != "claude":
            return None
        usage = sorted(
            [e for e in self._claude_usage_events(session_id) if e.ts], key=lambda e: e.ts
        )
        total_tokens = sum(e.input + e.output + e.cc for e in usage)
        total_cost = sum(self._event_cost(e) for e in usage)
        events_tl = self.get_events(session_id) or []
        user_turns = [e for e in events_tl if e.kind == "user" and e.timestamp]
        truncated = False
        if len(user_turns) > limit:  # evenly sample, always keep the last
            step = len(user_turns) / limit
            idx = sorted({int(i * step) for i in range(limit)} | {len(user_turns) - 1})
            user_turns = [user_turns[i] for i in idx]
            truncated = True
        points: list[UsagePoint] = []
        i = 0
        cum_tok = 0
        cum_cost = 0.0
        for turn in user_turns:
            while i < len(usage) and usage[i].ts <= turn.timestamp:
                e = usage[i]
                cum_tok += e.input + e.output + e.cc
                cum_cost += self._event_cost(e)
                i += 1
            points.append(UsagePoint(
                anchor_uuid=turn.anchor_uuid, timestamp=turn.timestamp,
                label=turn.label, cumulative_tokens=cum_tok,
                cumulative_cost_usd=round(cum_cost, 4),
            ))
        return UsageTimeline(
            session_id=session_id, points=points,
            total_tokens=total_tokens, total_cost_usd=round(total_cost, 4), truncated=truncated,
        )

    def list_subagents(self, session_id: str) -> Optional[list[SubagentUsage]]:
        """Per-subagent token + cost usage, with the parent step (anchor) that spawned
        each. Claude-only (others don't record subagents as separate transcripts)."""
        if provider_for(session_id).id != "claude":
            return None
        sub_events = [e for e in self._claude_usage_events(session_id) if e.is_subagent]
        subs = self._subagent_usage(sub_events)
        # Map agent_id -> spawning tool_use anchor, from the session timeline.
        spawn: dict[str, str] = {}
        for e in self.get_events(session_id) or []:
            if e.kind == "subagent" and e.subagent:
                spawn[e.subagent.agent_id] = e.anchor_uuid or e.tool_use_id or ""
        for s in subs:
            s.spawn_anchor_uuid = spawn.get(s.agent_id) or None
        return subs

    def get_persisted_output(
        self, session_id: str, cache_id: str, offset: int = 0, limit: Optional[int] = None
    ) -> Optional[dict]:
        paths = find_session(session_id)
        if paths is None:
            return None
        f = paths.tool_result_file(cache_id)
        if not f.is_file():
            return None
        size = f.stat().st_size
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            if offset:
                fh.seek(offset)
            content = fh.read() if limit is None else fh.read(limit)
            new_offset = fh.tell()
        return {
            "content": content,
            "offset": new_offset,
            "size_bytes": size,
            "truncated": new_offset < size,
        }

    # --- investigations (AI/user markup; muse-owned, never ~/.claude) -------
    def _suggest_session(self, sid: str) -> list[str]:
        """Find known session ids that are a near-miss for `sid` (a likely
        mis-transcription, e.g. 43ac↔43a3). Returns the closest few so the error
        can say 'did you mean …?' instead of just blaming the caller."""
        ids = [s.session_id for s in self.list_sessions()]

        def ham(a: str, b: str) -> int:
            return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))

        same_len = [i for i in ids if len(i) == len(sid) and ham(i, sid) <= 2]
        if same_len:
            return sorted(same_len, key=lambda i: ham(i, sid))[:3]
        pre = sorted((i for i in ids if i[:8] == sid[:8]), key=lambda i: ham(i, sid))
        return pre[:3]

    def _validate_refs(self, refs: list[dict]) -> None:
        """Reject references to sessions/steps that don't exist, so a hand-typed
        (mis-transcribed) id can't be persisted as a dead deep-link. Raises
        ValueError with guidance the caller surfaces to the agent."""
        anchors_by_session: dict[str, Optional[set]] = {}
        for r in refs:
            sid = (r.get("session_id") or "").strip()
            if not sid:
                raise ValueError("reference is missing session_id")
            if sid not in anchors_by_session:
                ev = self.get_events(sid)
                anchors_by_session[sid] = (
                    {a for e in ev for a in (e.anchor_uuid, e.tool_use_id) if a}
                    if ev is not None else None
                )
            anchors = anchors_by_session[sid]
            if anchors is None:
                near = self._suggest_session(sid)
                hint = (f" — did you mean {near[0]!r}?" if near else "")
                raise ValueError(
                    f"session_id {sid!r} did not resolve to a session{hint} "
                    "(copy ids exactly as returned by list_sessions/search_sessions; "
                    "a single wrong character produces a dead link)"
                )
            anchor = r.get("anchor_uuid")
            if anchor and anchor not in anchors:
                raise ValueError(
                    f"anchor_uuid {anchor!r} is not a step in session {sid} — use an "
                    "anchor exactly as returned by get_session/get_session_outline/get_step"
                )

    def create_investigation(
        self, title: str, body: str = "", author: str = "ai",
        status: str = "open", refs: Optional[list[dict]] = None,
        kind: str = "investigation",
    ) -> Investigation:
        self._validate_refs(refs or [])
        return self.investigations.create_investigation(title, body, author, status, refs, kind)

    def get_investigation(self, investigation_id: str) -> Optional[Investigation]:
        return self.investigations.get_investigation(investigation_id)

    def list_investigations(self) -> list[InvestigationSummary]:
        return self.investigations.list_investigations()

    def update_investigation(
        self, investigation_id: str, title: Optional[str] = None,
        body: Optional[str] = None, status: Optional[str] = None,
        append_body: Optional[str] = None,
    ) -> Optional[Investigation]:
        return self.investigations.update_investigation(
            investigation_id, title, body, status, append_body
        )

    def delete_investigation(self, investigation_id: str) -> bool:
        return self.investigations.delete_investigation(investigation_id)

    def add_reference(
        self, investigation_id: str, session_id: str, anchor_uuid: Optional[str] = None,
        label: str = "", comment: str = "",
    ) -> Optional[InvestigationRef]:
        self._validate_refs([{"session_id": session_id, "anchor_uuid": anchor_uuid}])
        return self.investigations.add_reference(
            investigation_id, session_id, anchor_uuid, label, comment
        )

    def remove_reference(self, ref_id: str) -> bool:
        return self.investigations.remove_reference(ref_id)

    def get_session_references(self, session_id: str) -> list[SessionBacklink]:
        return self.investigations.get_session_references(session_id)

    def reference_freshness(self, session_id: str) -> Optional[dict]:
        """How far a session has advanced past the latest step any investigation
        references — so a stale investigation (written mid-session) can be spotted.
        Returns None when there are no backlinks. `steps_after` is how many steps the
        session gained since the newest referenced anchor."""
        backlinks = self.investigations.get_session_references(session_id)
        if not backlinks:
            return None
        events = self.get_events(session_id)
        total = len(events) if events else 0
        pos: dict[str, int] = {}
        for i, e in enumerate(events or []):
            for a in (e.anchor_uuid, e.tool_use_id):
                if a and a not in pos:
                    pos[a] = i
        idxs = [
            pos[b.ref.anchor_uuid]
            for b in backlinks
            if b.ref.anchor_uuid and b.ref.anchor_uuid in pos
        ]
        last = max(idxs) if idxs else None
        return {
            "session_steps": total,
            "referencing_investigations": len({b.investigation_id for b in backlinks}),
            "last_referenced_step": last,
            "steps_after": (total - 1 - last) if last is not None else None,
        }

    # --- worklog notes (muse-owned; never ~/.claude) -------------------------
    def create_note(
        self,
        body: str,
        session_id: Optional[str] = None,
        anchor_uuid: Optional[str] = None,
        kind: str = "note",
        author: str = "user",
    ) -> Note:
        if session_id or anchor_uuid:
            if not session_id:
                raise ValueError("anchor_uuid requires a session_id")
            self._validate_refs([{"session_id": session_id, "anchor_uuid": anchor_uuid}])
        if not (body or "").strip():
            raise ValueError("note body is empty")
        if session_id:
            self._brief_cache.pop(session_id, None)  # notes feed the re-entry brief
        return self.worklog.create_note(body.strip(), session_id, anchor_uuid, kind, author)

    def list_notes(
        self,
        session_id: Optional[str] = None,
        day: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 200,
    ) -> list[Note]:
        return self.worklog.list_notes(session_id, day, kind, limit)

    def update_note(
        self, note_id: str, body: Optional[str] = None, kind: Optional[str] = None
    ) -> Optional[Note]:
        return self.worklog.update_note(note_id, body, kind)

    def delete_note(self, note_id: str) -> bool:
        return self.worklog.delete_note(note_id)

    def get_journal(self, day: str) -> dict:
        """One day of work: that day's notes interleaved with the sessions active
        that day (by mtime, from the cached session list — no extra scanning)."""
        notes = self.worklog.list_notes(day=day)
        day_sessions = [
            s for s in self.list_sessions()
            if s.mtime.astimezone().strftime("%Y-%m-%d") == day
        ]
        return {"day": day, "notes": notes, "sessions": day_sessions}

    # --- open loops ("continue working" rail) ---------------------------------
    _OPEN_LOOPS_DAYS = 7
    _OPEN_LOOPS_CAP = 12

    def get_open_loops(self) -> list[dict]:
        """Recently-active-but-unfinished sessions: waiting/stopped within the last
        7 days, newest first, sessions carrying an unresolved 'next' note on top.
        Each entry adds the last user intent + open-todo count from the (mtime-
        memoized) re-entry brief, so the poll is cheap after the first hit."""
        cutoff = time.time() - self._OPEN_LOOPS_DAYS * 86400
        candidates = [
            s for s in self.list_sessions()
            if s.state in ("waiting", "stopped") and s.mtime.timestamp() >= cutoff
        ]
        flagged = self.worklog.sessions_with_open_next()
        candidates.sort(
            key=lambda s: (s.session_id in flagged, s.mtime.timestamp()), reverse=True
        )
        out = []
        for s in candidates[: self._OPEN_LOOPS_CAP]:
            brief = self.build_reentry_brief(s.session_id) or {}
            out.append({
                "summary": s,
                "last_user_label": (brief.get("last_goal") or {}).get("text"),
                "open_todo_count": len(brief.get("open_todos") or []),
                "next_notes": brief.get("next_notes") or [],
                "open_error_count": len(brief.get("open_errors") or []),
            })
        return out

    # --- context packs + launcher ---------------------------------------------
    def create_pack(
        self,
        source_session_id: Optional[str] = None,
        include_brief: bool = True,
        note_ids: Optional[list[str]] = None,
        include_files: bool = True,
        extra_md: str = "",
        title: str = "",
    ):
        """Render a hand-off markdown pack from what muse already knows about a
        session and persist it under ~/.muse/packs/ (never the project dir)."""
        parts: list[str] = []
        src_title = None
        if source_session_id:
            summary = next(
                (s for s in self.list_sessions() if s.session_id == source_session_id),
                None,
            )
            src_title = summary.title if summary else source_session_id
            parts.append(
                f"# Context from previous session: {src_title}\n\n"
                f"(session `{source_session_id}`"
                + (f", project `{summary.project_cwd}`" if summary and summary.project_cwd else "")
                + ")"
            )
            if include_brief:
                brief = self.build_reentry_brief(source_session_id) or {}
                lines = ["## Where it left off"]
                if brief.get("last_goal"):
                    lines.append(f"**Last goal:** {brief['last_goal']['text']}")
                if brief.get("last_assistant"):
                    lines.append(f"**Last assistant word:** {brief['last_assistant']['text']}")
                for t in brief.get("open_todos") or []:
                    lines.append(f"- [ ] {t['content']} ({t['status']})")
                for err in brief.get("open_errors") or []:
                    lines.append(f"- ⚠ {err['label']}: {err['detail']}")
                if len(lines) > 1:
                    parts.append("\n".join(lines))
            if note_ids:
                notes = [
                    n for n in self.worklog.list_notes(session_id=source_session_id, limit=500)
                    if n.id in set(note_ids)
                ]
                if notes:
                    parts.append(
                        "## Worklog notes\n"
                        + "\n".join(f"- [{n.kind}] {n.body}" for n in notes)
                    )
            if include_files:
                changes_ = self.get_file_changes(source_session_id) or []
                if changes_:
                    parts.append(
                        "## Files that session touched\n"
                        + "\n".join(
                            f"- `{c.path}` ({c.read_count}r/{c.edit_count}e/{c.write_count}w)"
                            for c in changes_[:15]
                        )
                    )
        if extra_md.strip():
            parts.append(extra_md.strip())
        body = "\n\n".join(parts) or "(empty pack)"
        return self.packs.create(
            title or (f"Pack: {src_title}" if src_title else "Context pack"),
            body,
            source_session_id,
        )

    def launch_session(
        self, cwd: str, prompt: str, pack_id: Optional[str] = None
    ) -> dict:
        """Launch a NEW Claude Code session: tmux window when available, and in
        every case return the equivalent shell command for clipboard fallback."""
        import shlex

        full_prompt = prompt.strip()
        if pack_id:
            pack = self.packs.get(pack_id)
            if pack is None:
                return {"ok": False, "error": f"pack not found: {pack_id}", "command": ""}
            preamble = f"Read {pack.path} for context from my previous session"
            full_prompt = f"{preamble}, then: {full_prompt}" if full_prompt else preamble
        argv = ["claude", full_prompt] if full_prompt else ["claude"]
        command = f"cd {shlex.quote(cwd)} && {shlex.join(argv)}"
        if not tmux.available():
            return {"ok": False, "error": "tmux not available", "command": command}
        ok, result = tmux.new_window(cwd, shlex.join(argv))
        return {
            "ok": ok,
            "pane_id": result if ok else None,
            "error": None if ok else result,
            "command": command,
        }

    def launch_targets(self) -> list[str]:
        """Known project cwds (most recently active first) for the launch picker."""
        seen: list[str] = []
        for s in self.list_sessions():
            if s.project_cwd and s.project_cwd not in seen:
                seen.append(s.project_cwd)
        return seen[:40]

    # --- re-entry brief (deterministic "where you left off") -----------------
    def build_reentry_brief(self, session_id: str) -> Optional[dict]:
        """Everything needed to get back into a session, computed from data muse
        already has (no AI call): the last user goal, the open TodoWrite todos,
        recent files, errors since the last user turn, worklog notes, and staleness
        of any investigations. Memoized by (session_id, file mtime)."""
        summary = next(
            (s for s in self.list_sessions() if s.session_id == session_id), None
        )
        mtime = summary.mtime.timestamp() if summary else None
        cached = self._brief_cache.get(session_id)
        if cached and mtime is not None and cached[0] == mtime:
            return cached[1]
        if summary and summary.size_bytes > self._CACHE_MAX_BYTES:
            return None  # pathological transcript — never parse it for a banner

        events = self.get_events(session_id)
        if events is None:
            return None

        # Last real user turn (the goal being worked) + the assistant's last word.
        last_user = next((e for e in reversed(events) if e.kind == "user"), None)
        last_assistant = next(
            (e for e in reversed(events) if e.kind == "assistant_text"), None
        )

        # Open todos from the most recent TodoWrite — the best free "next steps".
        todos: list[dict] = []
        todo_call = next(
            (e for e in reversed(events)
             if e.kind == "tool_call" and e.tool_name == "TodoWrite"),
            None,
        )
        if todo_call and todo_call.tool_use_id:
            tu = self._tool_index(session_id).get(todo_call.tool_use_id)
            for t in ((tu.input or {}).get("todos") or []) if tu else []:
                if isinstance(t, dict) and t.get("content"):
                    todos.append({
                        "content": t["content"],
                        "status": t.get("status", "pending"),
                    })
        open_todos = [t for t in todos if t["status"] != "completed"]

        # Errors after the last user turn (still-unresolved failures).
        user_idx = last_user.index if last_user else -1
        open_errors = [
            {"label": e.label, "detail": (e.detail or "")[:300],
             "anchor_uuid": e.anchor_uuid or e.tool_use_id}
            for e in events
            if e.index > user_idx
            and (e.is_error or (e.kind == "system" and e.level == "error"))
        ][-5:]

        # Most recently touched files.
        changes = self.get_file_changes(session_id) or []
        changes.sort(
            key=lambda c: c.last_ts.timestamp() if c.last_ts else 0.0, reverse=True
        )
        files = [
            {"path": c.path, "reads": c.read_count, "edits": c.edit_count,
             "writes": c.write_count,
             "last_ts": c.last_ts.isoformat() if c.last_ts else None}
            for c in changes[:5]
        ]

        notes = self.worklog.list_notes(session_id=session_id, limit=10)
        latest_brief = next((n for n in notes if n.kind == "brief"), None)
        next_notes = [n for n in notes if n.kind == "next"]

        brief = {
            "session_id": session_id,
            "title": summary.title if summary else None,
            "provider": summary.provider if summary else None,
            "project_cwd": summary.project_cwd if summary else None,
            "state": summary.state if summary else None,
            "mtime": summary.mtime.isoformat() if summary else None,
            "idle_seconds": (
                max(0, int(time.time() - mtime)) if mtime is not None else None
            ),
            "last_goal": (
                {"text": (last_user.detail or last_user.label or "")[:600],
                 "anchor_uuid": last_user.anchor_uuid}
                if last_user else None
            ),
            "last_assistant": (
                {"text": (last_assistant.detail or last_assistant.label or "")[:600],
                 "anchor_uuid": last_assistant.anchor_uuid}
                if last_assistant else None
            ),
            "open_todos": open_todos,
            "done_todos": len(todos) - len(open_todos),
            "open_errors": open_errors,
            "files": files,
            "next_notes": [
                {"id": n.id, "body": n.body, "created_at": n.created_at}
                for n in next_notes
            ],
            "latest_ai_brief": (
                {"id": latest_brief.id, "body": latest_brief.body,
                 "created_at": latest_brief.created_at}
                if latest_brief else None
            ),
            "note_count": len(notes),
            "reference_freshness": self.reference_freshness(session_id),
            "resume_command": (
                f"claude --resume {session_id}"
                if summary and summary.provider == "claude" else None
            ),
        }
        if mtime is not None:
            self._brief_cache[session_id] = (mtime, brief)
        return brief

    def get_session_artifacts(self, session_id: str) -> Optional[dict]:
        """The session's working-dir notes (NOTES.md, *.md) and durable memory files
        — the freshest evidence, which often lives OUTSIDE the transcript. Returns
        bounded previews + paths (read-only); read a file in full off the returned
        path if needed. Memory files are Claude-only (its per-project memory dir)."""
        thread = self.get_thread(session_id)
        if thread is None:
            return None
        cwd = thread.project_cwd
        notes = artifacts.collect_notes(cwd)
        results = artifacts.collect_results(cwd)
        memory_dir = None
        paths = find_session(session_id)  # Claude: projects/<encoded-cwd>/memory
        if paths is not None:
            memory_dir = paths.root / "memory"
        memory = artifacts.collect_memory(memory_dir)
        out = {
            "session_id": session_id,
            "cwd": cwd,
            "memory_dir": str(memory_dir) if memory_dir and memory_dir.is_dir() else None,
            "notes": notes,
            "memory": memory,
            "results": self._fit_results(results, notes, memory),
            "results_dirs_found": len(results),  # may exceed len(results) returned (budget trim)
            "note": (
                "results dirs are ordered most-recently-active first and trimmed to a size "
                "budget (results_dirs_found shows the total). Each lists the LATEST run's files "
                "(a manifest, no content). notes/memory carry bounded head previews. Pull any "
                "path in full with read_artifact. These often supersede a mid-session investigation."
            ),
        }
        return out

    def _fit_results(self, results: list, notes: list, memory: list) -> list:
        """Trim the (variable-size) results manifest so the whole artifacts payload
        stays under the MCP char budget and returns inline — files are dropped from
        the tail with a per-dir `files_truncated` flag, never silently."""
        budget = self._MCP_RESULT_CHARS
        used = len(json.dumps(notes)) + len(json.dumps(memory))
        fitted = []
        for r in results:
            entry = {"results_dir": r["results_dir"], "run_count": r["run_count"],
                     "latest_run": r["latest_run"], "files": [], "files_truncated": False}
            used += len(json.dumps({**entry, "files": []}))
            for f in r["files"]:
                c = len(json.dumps(f)) + 1
                if used + c > budget:
                    entry["files_truncated"] = True
                    break
                entry["files"].append(f)
                used += c
            fitted.append(entry)
            if entry["files_truncated"]:
                break  # out of budget; stop adding more dirs
        return fitted

    def _artifact_roots(self, session_id: str) -> list:
        """Allowed read roots for read_artifact: the session's cwd + its memory dir."""
        roots = []
        thread = self.get_thread(session_id)
        if thread and thread.project_cwd:
            roots.append(Path(thread.project_cwd))
        paths = find_session(session_id)
        if paths is not None:
            roots.append(paths.root / "memory")
        return roots

    def read_artifact(
        self, session_id: str, path: str, offset: int = 0, limit: int = 20000
    ) -> Optional[dict]:
        """Read one artifact file in full (paginated), scoped to the session's project
        dir — the MCP path to evidence produced out-of-band (results/N/, NOTES.md, …)."""
        roots = self._artifact_roots(session_id)
        if not roots:
            return None
        return artifacts.read_artifact(roots, path, offset, limit)

    def build_session_digest(
        self, session_id: str, max_context_tokens: int = 16000
    ) -> Optional[DigestResult]:
        """Ordered, token-bounded trajectory digest — the MCP get_session output."""
        thread = self.get_thread(session_id)
        if thread is None:
            return None
        events = self.get_events(session_id) or []
        files = self.get_file_changes(session_id) or []
        return build_digest(thread, events, files, max_context_tokens=max_context_tokens)

    # --- live tailing --------------------------------------------------------
    async def subscribe(self, session_id: str):
        ok = await self.tailers.acquire(session_id)
        if not ok:
            return None
        return await self.broker.subscribe(session_id)

    async def unsubscribe(self, session_id: str, queue) -> None:
        await self.broker.unsubscribe(session_id, queue)
        await self.tailers.release(session_id)
