"""Autopilot controller: a guarded background loop that injects messages into
idle, tmux-matched Claude Code sessions, with context/compaction and usage
back-off policies."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import discovery as session_discovery
from .. import options as opt
from ..config import get_settings
from ..models import AutopilotConfig, AutopilotSession, AutopilotState
from ..usage_cache import context_pcts, scan_all
from . import sessions as live_discovery
from . import snapshot as layout_snapshot
from . import tmux
from .resettime import parse_reset_time
from .store import AutopilotStore

TICK_SECONDS = 5
# Layout snapshots for session-restore: check the topology at most this often, and only
# write when the structure changed (or a long backstop elapsed) — change-driven, so it
# doesn't churn while you're just working inside a session.
SNAPSHOT_CHECK_SECONDS = 25
SNAPSHOT_BACKSTOP_SECONDS = 1800
INJECT_STATUSES = {"idle"}  # only when a turn finished and it's awaiting the user
# Minimum gap between queued-reply deliveries to the same session: after a send
# the status file takes a moment to flip to busy, so without a floor the next
# tick could double-fire into the same idle turn.
QUEUE_COOLDOWN_SECONDS = 15


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
        # Optional observer for limit-hit sightings: on_limit("5h"|"week") records
        # the window's spend as an observed ceiling (subscription plans publish no
        # $ caps, so the wall's location is only learnable by hitting it).
        self.on_limit = None
        # AI idle-mode callables (wired in main.py to the SessionService — same
        # pattern as on_reset, avoiding an import cycle):
        #   enqueue_draft(sid) -> AIJob | None
        #   get_ai_job(job_id) -> AIJob | None
        #   ai_cost_today() -> float
        self.enqueue_draft = None
        self.get_ai_job = None
        self.ai_cost_today = None
        # Last queued-reply delivery per session (monotonic secs), for the cooldown.
        self._queue_sent_at: dict[str, float] = {}
        # Layout-snapshot bookkeeping (monotonic secs): when we last checked/wrote.
        self._last_snapshot_check = 0.0
        self._last_snapshot_write = 0.0

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

    # --- queued replies -------------------------------------------------------
    # Deliver user-authored "send when this turn ends" messages. This runs inside
    # the controller because the controller is the one disciplined automated
    # tmux-write site — same guards as autopilot injection (idle status, pane
    # re-check, rate-limit banner, visible-menu refusal), but it does NOT require
    # autopilot to be armed: queueing was an explicit user action.

    def _queue_block_reason(
        self, sid: str, live: dict, now_mono: float, *, force: bool = False
    ) -> Optional[str]:
        """Why the next queued reply for `sid` can't be delivered right now, or
        None if it would go. `force` (a user's explicit "send now") skips the
        wait-for-idle gate but never overrides the guards that would corrupt the
        session — typing into an open menu or over a usage-limit banner."""
        ls = live.get(sid)
        if ls is None or not ls.pane_id:
            return "session isn’t running in tmux"
        if not force:
            if ls.waiting_for:
                return f"waiting for {ls.waiting_for}"
            if ls.status not in INJECT_STATUSES:
                return f"session is {ls.status}, not idle"
            if now_mono - self._queue_sent_at.get(sid, 0.0) < QUEUE_COOLDOWN_SECONDS:
                return "just delivered — cooling down"
        pane = tmux.capture_pane(ls.pane_id, 40)
        if _RATE_LIMIT_RE.search(pane):
            self._observe_limit(pane)
            return "usage-limit banner on screen"
        # A visible ❯ menu means typed text would land IN the dialog (digits
        # select options!) — never deliver over one, even when forced.
        if opt.parse_permission_menu(pane) is not None:
            return "a menu is open — answer it first"
        return None

    def _send_batch(self, sid: str, pane_id: str, now_mono: float) -> tuple[list[int], Optional[str]]:
        """Deliver the oldest pending item plus any directly-following 'append'
        items (they asked to share one message). Returns (sent ids, error)."""
        batch = self.store.queue_next_batch(sid)
        if not batch:
            return [], None
        combined = "\n".join(i.text for i in batch)
        if "\n" in combined:
            # Multiline goes as a bracketed paste — raw LF via send-keys would
            # submit each line as its own message.
            ok, err = tmux.paste_text(pane_id, combined)
        else:
            ok, err = tmux.send_text(pane_id, combined)
        if ok:
            self._queue_sent_at[sid] = now_mono
            for i in batch:
                self.store.queue_mark(i.id, "sent")
            self.store.log(
                sid, "queue_sent",
                f"{pane_id} ← {combined[:80]}"
                + (f" ({len(batch)} items)" if len(batch) > 1 else ""),
            )
            return [i.id for i in batch], None
        for i in batch:
            self.store.queue_mark(i.id, "failed", error=err)
        self.store.log(sid, "error", f"queued send failed: {err}")
        return [], err

    def deliver_queued(self, session_id: Optional[str] = None) -> list[int]:
        """Try to deliver the next queued reply for `session_id` (or for every
        session with a pending queue). Returns the queue ids actually sent.
        Safe to call from any thread; each delivery re-checks the world fresh."""
        counts = self.store.queue_counts()
        if session_id is not None:
            counts = {session_id: counts[session_id]} if session_id in counts else {}
        if not counts:
            return []
        live = {s.session_id: s for s in live_discovery.discover()}
        now_mono = time.monotonic()
        sent: list[int] = []
        for sid in counts:
            if self._queue_block_reason(sid, live, now_mono) is not None:
                continue
            ids, _ = self._send_batch(sid, live[sid].pane_id, now_mono)
            sent.extend(ids)
        return sent

    def deliver_now(self, session_id: str) -> tuple[list[int], Optional[str]]:
        """User override: deliver the next batch immediately, skipping the
        wait-for-idle gate but not the menu / rate-limit guards. Returns
        (sent ids, reason-it-was-blocked)."""
        if session_id not in self.store.queue_counts():
            return [], "nothing queued"
        live = {s.session_id: s for s in live_discovery.discover()}
        now_mono = time.monotonic()
        reason = self._queue_block_reason(session_id, live, now_mono, force=True)
        if reason is not None:
            return [], reason
        return self._send_batch(session_id, live[session_id].pane_id, now_mono)

    def queue_hold_reason(self, session_id: str) -> Optional[str]:
        """Why this session's pending queue isn't delivering (for the UI), or
        None if it has no pending items or would deliver on the next tick."""
        if session_id not in self.store.queue_counts():
            return None
        live = {s.session_id: s for s in live_discovery.discover()}
        return self._queue_block_reason(session_id, live, time.monotonic())

    def _maybe_snapshot(self) -> None:
        """Capture the tmux topology for session-restore, throttled and change-driven.
        Runs regardless of arming (it's independent of message injection)."""
        now = time.monotonic()
        if now - self._last_snapshot_check < SNAPSHOT_CHECK_SECONDS:
            return
        self._last_snapshot_check = now
        snap = layout_snapshot.build_snapshot()
        if snap is None:  # tmux down or no Claude windows → keep the last-known-good
            return
        sig = layout_snapshot.topology_sig(snap)
        latest = self.store.latest_snapshot()
        unchanged = latest is not None and latest[0] == sig
        if unchanged and (now - self._last_snapshot_write) < SNAPSHOT_BACKSTOP_SECONDS:
            return
        self.store.save_snapshot(json.dumps(snap), sig)
        self._last_snapshot_write = now

    def _tick(self) -> None:
        # Session-restore snapshot — independent of arming; never let it break the loop.
        try:
            self._maybe_snapshot()
        except Exception:
            pass
        # Queued replies deliver regardless of arming/schedule — an explicit user
        # "send this when ready" beats the autopilot on/off switch.
        try:
            self.deliver_queued()
        except Exception:
            pass
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
                reset = self._observe_limit(pane, now)
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

    def _observe_limit(self, pane_text: str, now: Optional[datetime] = None):
        """A rate-limit banner is on screen: report the parsed reset time (anchors
        stats' 5h window) and the hit itself (calibrates the observed ceiling).
        Returns the parsed reset time, if any."""
        reset = parse_reset_time(pane_text, now or datetime.now(timezone.utc))
        if reset and self.on_reset:
            try:
                self.on_reset(reset)
            except Exception:
                pass
        if self.on_limit:
            try:
                self.on_limit("week" if "week" in pane_text.lower() else "5h")
            except Exception:
                pass
        return reset

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
