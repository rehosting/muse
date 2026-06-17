"""Autopilot controller: a guarded background loop that injects messages into
idle, tmux-matched Claude Code sessions, with context/compaction and usage
back-off policies."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import discovery as session_discovery
from ..config import get_settings
from ..models import AutopilotConfig, AutopilotSession, AutopilotState
from ..usage_cache import context_pcts, scan_all
from . import sessions as live_discovery
from . import tmux
from .resettime import parse_reset_time
from .store import AutopilotStore

TICK_SECONDS = 5
INJECT_STATUSES = {"idle"}  # only when a turn finished and it's awaiting the user


def _dt_or_none(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None

# Phrases Claude Code shows when a usage/rate limit is hit.
_RATE_LIMIT_RE = re.compile(
    r"(usage limit|rate limit|out of credits|5-hour limit|weekly limit|limit reached|"
    r"reached your|approaching your .*limit|resets at|try again (later|after)|upgrade to continue)",
    re.IGNORECASE,
)


class AutopilotController:
    def __init__(self) -> None:
        self.store = AutopilotStore(get_settings().db_path)
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._cache: Optional[AutopilotState] = None
        self._cache_ts = 0.0
        # Optional observer for parsed usage-limit reset times (wired to the
        # usage-history store in main.py so stats can anchor the 5h window).
        self.on_reset = None
        # AI idle-mode callables (wired in main.py to the SessionService — same
        # pattern as on_reset, avoiding an import cycle):
        #   enqueue_draft(sid) -> AIJob | None
        #   get_ai_job(job_id) -> AIJob | None
        #   ai_cost_today() -> float
        self.enqueue_draft = None
        self.get_ai_job = None
        self.ai_cost_today = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._stop.set()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.store.close()

    # --- public API ---------------------------------------------------------
    def get_state(self) -> AutopilotState:
        # Short TTL so overlapping/rapid polls (multiple tabs) reuse one
        # computation instead of each re-scanning tmux + transcripts.
        if self._cache is not None and (time.monotonic() - self._cache_ts) < 1.5:
            return self._cache
        return self._refresh()

    def _refresh(self) -> AutopilotState:
        state = self._compute_state()
        self._cache = state
        self._cache_ts = time.monotonic()
        return state

    def _compute_state(self) -> AutopilotState:
        live = {s.session_id: s for s in live_discovery.discover()}
        configs = self.store.all_configs()
        titles = {s.session_id: s.title for s in session_discovery.list_sessions()}

        sids = set(live) | set(configs)
        out = []
        for sid in sids:
            ls = live.get(sid)
            out.append(
                AutopilotSession(
                    session_id=sid,
                    title=titles.get(sid) or (ls.cwd.split("/")[-1] if ls and ls.cwd else None),
                    live=ls,
                    config=configs.get(sid) or self.store.get_config(sid),
                )
            )
        out.sort(key=lambda a: (a.live is None, not a.config.enabled))
        enabled, start, end = self.store.get_schedule()
        return AutopilotState(
            armed=self.store.is_armed(),
            tmux_available=tmux.available(),
            schedule_enabled=enabled,
            schedule_start_hour=start,
            schedule_end_hour=end,
            within_hours=self._within_hours(),
            sessions=out,
            recent_log=self.store.recent_log(50),
        )

    def set_armed(self, armed: bool) -> AutopilotState:
        self.store.set_armed(armed)
        self.store.log("-", "armed" if armed else "disarmed", "")
        return self._refresh()

    def set_schedule(self, enabled: bool, start_hour: int, end_hour: int) -> AutopilotState:
        self.store.set_schedule(enabled, start_hour % 24, end_hour % 24)
        return self._refresh()

    def _within_hours(self) -> bool:
        enabled, start, end = self.store.get_schedule()
        if not enabled or start == end:
            return True
        h = datetime.now().astimezone().hour
        return start <= h < end if start < end else (h >= start or h < end)

    def apply_policy(self, session_ids: list[str], policy: dict) -> AutopilotState:
        for sid in session_ids:
            cfg = AutopilotConfig(session_id=sid, **policy)
            self.store.upsert_config(cfg)
        return self._refresh()

    def manual_send(self, sid: str) -> tuple[bool, str]:
        ls = {s.session_id: s for s in live_discovery.discover()}.get(sid)
        if ls is None:
            return False, "session is not active"
        if not ls.pane_id:
            return False, "no tmux pane matched"
        cfg = self.store.get_config(sid)
        if cfg.idle_mode == "suggestion":
            ok, err = tmux.accept_suggestion(ls.pane_id)
            self.store.log(sid, "manual" if ok else "error", err or f"{ls.pane_id}: accepted suggestion")
            return ok, err
        if not cfg.message.strip():
            return False, "no message configured"
        ok, err = tmux.send_text(ls.pane_id, cfg.message)
        self.store.log(sid, "manual" if ok else "error", err or f"{ls.pane_id}: {cfg.message[:80]}")
        return ok, err

    # --- loop ---------------------------------------------------------------
    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._tick)
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    # Context % computation lives in usage_cache.context_pcts (shared with the
    # board ticker).
    _context_pcts = staticmethod(context_pcts)

    def _tick(self) -> None:
        if not self.store.is_armed() or not self._within_hours():
            return
        configs = self.store.all_configs()
        if not any(c.enabled for c in configs.values()):
            return
        live = {s.session_id: s for s in live_discovery.discover()}
        ctx_pcts = self._context_pcts(scan_all())
        now = datetime.now(timezone.utc)

        for sid, cfg in configs.items():
            if not cfg.enabled:
                continue
            ls = live.get(sid)
            if ls is None or not ls.pane_id:
                continue
            # AI idle-mode Phase B runs BEFORE the one-send-per-turn gate: the
            # pending draft was requested against the CURRENT turn, which that
            # gate would now block.
            if cfg.idle_mode == "ai":
                job_id, req_upd = self.store.get_ai_pending(sid)
                if job_id:
                    self._ai_phase_b(sid, cfg, ls, job_id, req_upd, now)
                    continue
            if cfg.sent_count >= cfg.max_sends:
                continue
            if ls.status not in INJECT_STATUSES or ls.waiting_for:
                continue
            if cfg.backoff_until and now < cfg.backoff_until:
                continue
            if cfg.last_sent_at and (now - cfg.last_sent_at).total_seconds() < cfg.interval_seconds:
                continue
            # One send per turn: require new activity since our last send.
            last_seen = self.store.last_seen_updated_at(sid)
            if last_seen and ls.updated_at and ls.updated_at <= last_seen:
                continue

            # Usage-limit back-off: peek at the pane before acting.
            pane = tmux.capture_pane(ls.pane_id, 40)
            if _RATE_LIMIT_RE.search(pane):
                reset = parse_reset_time(pane, now)
                if reset and self.on_reset:
                    try:
                        self.on_reset(reset)  # anchor stats' 5h window
                    except Exception:
                        pass
                until = reset if (reset and reset > now) else now + timedelta(seconds=cfg.backoff_seconds)
                self.store.set_backoff(sid, until)
                when = until.astimezone().strftime("%a %H:%M")
                self.store.log(
                    sid,
                    "backoff",
                    f"usage limit — backing off until {when}" + (" (from reset time)" if reset else ""),
                )
                continue

            # Context / compaction policy takes priority when context is high.
            pct = ctx_pcts.get(sid)
            if pct is not None and pct >= cfg.context_threshold_pct and cfg.context_action != "none":
                self._do_context_action(sid, cfg, ls, pct)
                continue

            # Otherwise the normal "keep going" action.
            if cfg.idle_mode == "ai":
                # Phase A: all the gates above passed exactly as they would for
                # a message-mode send — request a draft instead of sending.
                # (Phase B, _ai_phase_b below, sends it on a later tick after
                # re-checking everything fresh.)
                self._ai_phase_a(sid, ls)
                continue
            if cfg.idle_mode == "suggestion":
                ok, err = tmux.accept_suggestion(ls.pane_id)
                detail = f"{ls.pane_id} ← (accepted Claude's suggestion)"
            elif cfg.message.strip():
                ok, err = tmux.send_text(ls.pane_id, cfg.message)
                detail = f"{ls.pane_id} ← {cfg.message[:80]}"
            else:
                continue
            if ok:
                self.store.record_send(sid, ls.updated_at)
                self.store.log(sid, "injected", detail)
            else:
                self.store.log(sid, "error", err)

    # --- AI idle mode (two-phase) ---------------------------------------------
    # Phase A requests a draft at exactly the point message-mode would inject
    # (so every existing gate — enabled/idle/no-waiting_for/max_sends/interval/
    # backoff/one-send-per-turn — has already passed). Phase B, on a later tick,
    # re-checks the world before typing anything into the pane. The controller
    # stays the ONLY autopilot tmux-write site; the AI worker never touches tmux.

    _AI_DRAFT_MAX_AGE = timedelta(minutes=10)

    def _ai_budget_left(self) -> bool:
        budget = get_settings().ai_daily_budget_usd
        if budget <= 0:
            return False  # ≤0 disables ai mode entirely
        if self.ai_cost_today is None:
            return False
        try:
            return self.ai_cost_today() < budget
        except Exception:
            return False

    def _ai_phase_a(self, sid: str, ls) -> None:
        if self.enqueue_draft is None:
            return
        if not self._ai_budget_left():
            self.store.log(sid, "ai_discarded", "daily AI budget exhausted (or ai disabled)")
            return
        try:
            job = self.enqueue_draft(sid)
        except Exception as e:
            self.store.log(sid, "error", f"draft enqueue failed: {e}")
            return
        if job is None:
            return
        self.store.set_ai_pending(sid, job.id, ls.updated_at)
        self.store.log(sid, "ai_requested", f"draft job {job.id}")

    def _ai_phase_b(self, sid: str, cfg: AutopilotConfig, ls, job_id: str,
                    requested_updated_at, now) -> None:
        job = self.get_ai_job(job_id) if self.get_ai_job else None

        def discard(reason: str) -> None:
            self.store.set_ai_pending(sid, None, None)
            self.store.log(sid, "ai_discarded", f"{job_id}: {reason}")

        if job is None:
            return discard("job vanished")
        if job.status in ("error", "cancelled"):
            return discard(f"job {job.status}: {str(job.error or '')[:80]}")
        if job.status in ("queued", "running"):
            created = _dt_or_none(job.created_at)
            if created and (now - created) > self._AI_DRAFT_MAX_AGE:
                return discard("draft took >10 min — stale")
            return  # still cooking; check again next tick
        # status == done — re-check EVERYTHING fresh before typing.
        draft = (job.result or {}).get("draft", "")
        if not draft.strip():
            return discard("empty draft")
        if ls.status not in INJECT_STATUSES or ls.waiting_for:
            return discard(f"session no longer idle ({ls.status}/{ls.waiting_for})")
        if requested_updated_at and ls.updated_at and ls.updated_at != requested_updated_at:
            return discard("session moved on since the draft was requested")
        if cfg.sent_count >= cfg.max_sends:
            return discard("max_sends reached")
        pane = tmux.capture_pane(ls.pane_id, 40)
        if _RATE_LIMIT_RE.search(pane):
            return discard("rate-limit banner on pane")
        ok, err = tmux.send_text(ls.pane_id, draft)
        self.store.set_ai_pending(sid, None, None)
        if ok:
            self.store.record_send(sid, ls.updated_at)
            self.store.log(sid, "ai_injected", f"{ls.pane_id} ← {draft[:80]}")
        else:
            self.store.log(sid, "error", f"ai send failed: {err}")

    def _do_context_action(self, sid: str, cfg: AutopilotConfig, ls, pct: float) -> bool:
        act = cfg.context_action
        if act == "stop":
            self.store.set_enabled(sid, False)
            self.store.log(sid, "stopped", f"context {pct:.0f}% ≥ {cfg.context_threshold_pct}%")
            return False
        text = (
            "/compact"
            if act == "compact"
            else "/clear"
            if act == "clear"
            else (cfg.context_message or cfg.message)
        )
        if not text.strip():
            return False
        ok, err = tmux.send_text(ls.pane_id, text)
        if ok:
            self.store.record_send(sid, ls.updated_at)
            self.store.log(sid, "context", f"{act} at {pct:.0f}% ctx → {text[:40]}")
        else:
            self.store.log(sid, "error", err)
        return False
