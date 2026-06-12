"""Per-file usage extraction with an mtime cache.

Reading + JSON-parsing every transcript on every /api/stats call is the slow
part. Here we extract a compact list of usage events per file and cache it keyed
by (path, mtime), so unchanged transcripts are parsed once. Both stats.py and
usage_insights.py consume the same cached events (eliminating a second scan).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import get_settings
from .incremental import new_objects


@dataclass
class Event:
    sid: str
    project_dir: str
    ts: Optional[datetime]
    input: int
    output: int
    cc: int
    cr: int
    model: Optional[str]
    is_subagent: bool
    agent_type: str
    tools: list[str] = field(default_factory=list)
    uuid: Optional[str] = None  # assistant message uuid (anchor for cost-at-step)
    agent_id: str = ""  # subagent id (filename stem), "" for the main thread

    @property
    def total(self) -> int:
        return self.input + self.output + self.cc + self.cr

    @property
    def context(self) -> int:
        return self.input + self.cc + self.cr


@dataclass
class Scan:
    events: list[Event]
    sessions: int
    sessions_by_project: dict[str, int]


def _parse_ts(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# path -> (mtime, offset, events, seen_msg_ids) — offset enables incremental
# (append-only) reads; seen ids must persist across appends so dedupe holds.
_file_cache: dict[Path, tuple[float, int, list[Event], set[str]]] = {}
_meta_cache: dict[Path, tuple[float, str]] = {}


def _file_events(
    path: Path, sid: str, project_dir: str, is_sub: bool, agent_type: str, agent_id: str = ""
) -> list[Event]:
    try:
        st = path.stat()
    except OSError:
        return []
    mtime, size = st.st_mtime, st.st_size
    cached = _file_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[2]

    # Resume from the cached offset if the file only grew (append); else re-read.
    base_offset = cached[1] if (cached and size >= cached[1]) else 0
    events: list[Event] = list(cached[2]) if (cached and base_offset > 0) else []
    seen: set[str] = set(cached[3]) if (cached and base_offset > 0) else set()
    objs, new_offset = new_objects(path, base_offset)
    for obj in objs:
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        tools = [
            b.get("name", "unknown")
            for b in (msg.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        usage = msg.get("usage")
        u = usage if isinstance(usage, dict) else {}
        # One API response is streamed as SEVERAL transcript lines (one per
        # content block), each repeating the SAME usage object under the same
        # message id. Count usage once per message id — summing every line
        # double-counts (it inflated totals ~2× vs Claude Code's own rollup).
        msg_id = msg.get("id")
        if u and msg_id:
            if msg_id in seen:
                u = {}
            else:
                seen.add(msg_id)
        if not u and not tools:
            continue
        events.append(
            Event(
                sid=sid,
                project_dir=project_dir,
                ts=_parse_ts(obj.get("timestamp")),
                input=u.get("input_tokens", 0) or 0,
                output=u.get("output_tokens", 0) or 0,
                cc=u.get("cache_creation_input_tokens", 0) or 0,
                cr=u.get("cache_read_input_tokens", 0) or 0,
                model=msg.get("model"),
                is_subagent=is_sub,
                agent_type=agent_type,
                tools=tools,
                uuid=obj.get("uuid"),
                agent_id=agent_id,
            )
        )
    _file_cache[path] = (mtime, new_offset, events, seen)
    return events


def _agent_type(sub_path: Path) -> str:
    meta = sub_path.with_suffix(".meta.json")
    try:
        mtime = meta.stat().st_mtime
    except OSError:
        return "subagent"
    cached = _meta_cache.get(meta)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        at = json.loads(meta.read_text(encoding="utf-8")).get("agentType", "subagent")
    except (OSError, json.JSONDecodeError):
        at = "subagent"
    _meta_cache[meta] = (mtime, at)
    return at


def context_pcts(scan: Scan) -> dict[str, float]:
    """Latest main-thread context size as a % of the model window, per session.
    (Lifted out of AutopilotController so the board ticker shares it.)"""
    latest: dict[str, tuple[datetime, int]] = {}
    peak: dict[str, int] = {}
    for e in scan.events:
        if e.is_subagent or e.ts is None or e.context <= 0:
            continue
        cur = latest.get(e.sid)
        if cur is None or e.ts > cur[0]:
            latest[e.sid] = (e.ts, e.context)
        peak[e.sid] = max(peak.get(e.sid, 0), e.context)
    out = {}
    for sid, (_ts, ctx) in latest.items():
        window = 1_000_000 if peak[sid] > 200_000 else 200_000
        out[sid] = 100.0 * ctx / window
    return out


# Per-FILE aggregate cache keyed by mtime: when a live session appends, only
# that file's events are re-summed — the rest of the corpus is a dict lookup.
# (A corpus-generation key was tried first and was a trap: any live append
# invalidated it, so every 3s board tick re-summed ~all events + priced each.)
# path -> (mtime, sid, work_tokens, cost_usd, latest_main_ts, latest_main_ctx,
#          peak_main_ctx)
_file_agg_cache: dict[Path, tuple] = {}


def _file_aggregate(path: Path, events: list[Event]) -> tuple:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _file_agg_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached
    from .pricing import cost_usd  # local import: avoid cycle at module load

    sid = events[0].sid if events else ""
    tokens = 0
    cost = 0.0
    latest_ts = None
    latest_ctx = 0
    peak_ctx = 0
    for e in events:
        tokens += e.input + e.output + e.cc  # real work (excl. cache reads)
        cost += cost_usd(e.model, e.input, e.output, e.cc, e.cr)
        if not e.is_subagent and e.ts is not None and e.context > 0:
            if latest_ts is None or e.ts > latest_ts:
                latest_ts, latest_ctx = e.ts, e.context
            peak_ctx = max(peak_ctx, e.context)
    agg = (mtime, sid, tokens, cost, latest_ts, latest_ctx, peak_ctx)
    _file_agg_cache[path] = agg
    return agg


def board_rollup() -> tuple[dict[str, tuple[int, float]], dict[str, float]]:
    """(sid -> (work_tokens, cost_usd), sid -> context_pct) for the board tick.
    O(#files) per call thanks to the per-file mtime-keyed aggregate cache."""
    projects = get_settings().projects_dir
    aggs: dict[str, tuple[int, float]] = {}
    latest: dict[str, tuple] = {}  # sid -> (ts, ctx)
    peak: dict[str, int] = {}
    if not projects.is_dir():
        return aggs, {}

    def feed(path: Path, sid: str, project: str, is_sub: bool, atype: str, aid: str = ""):
        a = _file_aggregate(path, _file_events(path, sid, project, is_sub, atype, aid))
        tokens, cost = aggs.get(sid, (0, 0.0))
        aggs[sid] = (tokens + a[2], cost + a[3])
        if a[4] is not None:
            cur = latest.get(sid)
            if cur is None or a[4] > cur[0]:
                latest[sid] = (a[4], a[5])
            peak[sid] = max(peak.get(sid, 0), a[6])

    for project_dir in projects.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            feed(jsonl, jsonl.stem, project_dir.name, False, "")
        for sub in project_dir.glob("*/subagents/agent-*.jsonl"):
            feed(sub, sub.parent.parent.name, project_dir.name, True,
                 _agent_type(sub), sub.stem)

    pcts = {}
    for sid, (_ts, ctx) in latest.items():
        window = 1_000_000 if peak.get(sid, 0) > 200_000 else 200_000
        pcts[sid] = 100.0 * ctx / window
    return aggs, pcts


def scan_all() -> Scan:
    """All usage events across every session + subagent (mtime-cached per file)."""
    projects = get_settings().projects_dir
    events: list[Event] = []
    sessions = 0
    by_project: dict[str, int] = {}
    if not projects.is_dir():
        return Scan(events, 0, by_project)
    for project_dir in projects.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            sessions += 1
            by_project[project_dir.name] = by_project.get(project_dir.name, 0) + 1
            events.extend(_file_events(jsonl, jsonl.stem, project_dir.name, False, ""))
        for sub in project_dir.glob("*/subagents/agent-*.jsonl"):
            sid = sub.parent.parent.name
            # Full stem ("agent-<id>") matches SubagentRef.agent_id from the
            # transcript, so spawn-step linking lines up.
            events.extend(
                _file_events(sub, sid, project_dir.name, True, _agent_type(sub), sub.stem)
            )
    return Scan(events, sessions, by_project)
