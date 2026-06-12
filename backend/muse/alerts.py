"""Alerts watcher: a background loop that fires push notifications when a
session needs you or breaks.

It reuses the cheap building blocks already in muse — `discovery.list_sessions`
for per-session state (live/waiting/stopped) and `incremental.new_objects` to
scan only the bytes appended since the last tick for errors — and delivers via
the notification seam (`SessionService.send_notification` → ntfy).

State/offset is primed on the first tick so enabling muse doesn't fire a burst
of alerts for sessions that were already waiting/stopped/errored.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import db
from .incremental import new_objects
from .models import AlertEvent
from .paths import SessionPaths

# How often (seconds) the watcher truncates the WAL. wal_autocheckpoint is the real
# backstop; this just keeps muse.db-wal small on the one always-on writer-cadence loop.
_CHECKPOINT_INTERVAL = 60.0
_USAGE_WARM_INTERVAL = 60.0  # mtime-cached, so warm calls only parse changed files
_AI_SCHEDULE_INTERVAL = 900.0  # auto-digest check cadence (opt-in via MUSE_AI_AUTO_DIGEST)


def _scan_errors(objs: list[dict]) -> list[str]:
    """Short descriptions of any error indicators in a batch of raw lines."""
    out: list[str] = []
    for obj in objs:
        if obj.get("isApiErrorMessage"):
            out.append(f"API error: {str(obj.get('content') or '')[:140]}")
            continue
        if obj.get("type") == "system" and obj.get("level") == "error":
            out.append(f"System error: {str(obj.get('content') or '')[:140]}")
            continue
        if obj.get("type") == "user":
            content = obj.get("message", {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        text = b.get("content")
                        if isinstance(text, list):
                            text = " ".join(
                                str(p.get("text", "")) for p in text if isinstance(p, dict)
                            )
                        out.append(f"Tool error: {str(text or '')[:140]}")
    return out


class AlertsWatcher:
    def __init__(self, service) -> None:
        self.service = service
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._states: dict[str, str] = {}
        self._offsets: dict[str, int] = {}
        self._primed = False
        self._log: deque[AlertEvent] = deque(maxlen=100)
        self._last_checkpoint = 0.0
        self._last_usage_warm = 0.0
        self._last_ai_schedule = 0.0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._primed = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._stop.set()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def recent_log(self, n: int = 50) -> list[AlertEvent]:
        return list(self._log)[-n:][::-1]

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                pass
            delay = max(5, self.service.get_alert_rules().poll_seconds)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        rules = self.service.get_alert_rules()
        cfg = self.service.get_notify_config()
        # Keep the search index warm off the request path so a keystroke-search
        # is just a fast FTS read (cheap: only changed transcripts re-index).
        try:
            await asyncio.to_thread(self.service.refresh_search_index)
        except Exception:
            pass
        # Keep the cross-session file-activity index warm too (cheap: only sessions
        # whose mtime advanced re-extract, rate-limited per session inside sync).
        try:
            await asyncio.to_thread(self.service.refresh_file_index)
        except Exception:
            pass
        # Re-score failure patterns (retry loops / error spirals / denials) for
        # changed sessions so the list can badge health from one table read.
        try:
            await asyncio.to_thread(self.service.refresh_health_index)
        except Exception:
            pass
        # Harvest git commits from known project repos + rematch provenance
        # (read-only `git log`; rate-limited per repo, capped per tick).
        try:
            await asyncio.to_thread(self.service.refresh_git_index)
        except Exception:
            pass
        # Keep the usage cache warm (so /api/stats never pays the cold full-corpus
        # parse on the request path) AND roll per-day usage into the persistent
        # history table so long-range stats survive transcript deletion.
        if time.monotonic() - self._last_usage_warm >= _USAGE_WARM_INTERVAL:
            self._last_usage_warm = time.monotonic()
            try:
                await asyncio.to_thread(self.service.roll_usage_history)
            except Exception:
                pass
        # Opt-in auto digests: once yesterday's sessions exist with no AI brief,
        # enqueue a daily digest (Mondays additionally draft last week's retro).
        if time.monotonic() - self._last_ai_schedule >= _AI_SCHEDULE_INTERVAL:
            self._last_ai_schedule = time.monotonic()
            try:
                await asyncio.to_thread(self._schedule_ai_digests)
            except Exception:
                pass
        # Periodically truncate the shared WAL so it can't balloon (it once hit 462MB).
        now = time.monotonic()
        if now - self._last_checkpoint >= _CHECKPOINT_INTERVAL:
            self._last_checkpoint = now
            try:
                await asyncio.to_thread(db.checkpoint, self.service.search_index._conn)
            except Exception:
                pass
        # list_sessions() is TTL-cached; safe to call every tick.
        summaries = await asyncio.to_thread(self.service.list_sessions)

        first = not self._primed
        seen = set()
        for s in summaries:
            sid = s.session_id
            seen.add(sid)
            path = SessionPaths(project_dir=s.project_dir, session_id=sid).jsonl

            # --- errors: scan only the appended bytes ---
            prev_off = self._offsets.get(sid)
            if first or prev_off is None:
                # Prime the offset to current size; don't scan history.
                try:
                    self._offsets[sid] = path.stat().st_size
                except OSError:
                    self._offsets[sid] = 0
            else:
                objs, new_off = await asyncio.to_thread(new_objects, path, prev_off)
                self._offsets[sid] = new_off
                if objs and rules.on_error:
                    errs = _scan_errors(objs)
                    if errs:
                        await self._fire(
                            cfg, rules, s, "error",
                            f"⚠ {s.title}: {len(errs)} error(s)",
                            errs[0],
                        )

            # --- state transitions (waiting / stopped) ---
            prev = self._states.get(sid)
            self._states[sid] = s.state
            if first or prev is None or prev == s.state:
                continue
            if s.state == "waiting" and rules.on_waiting:
                await self._fire(cfg, rules, s, "waiting", f"✋ {s.title} is waiting for you", "")
            elif s.state == "stopped" and rules.on_stopped:
                await self._fire(cfg, rules, s, "stopped", f"⏹ {s.title} stopped", "")

        # Forget sessions that disappeared.
        for sid in list(self._states):
            if sid not in seen:
                self._states.pop(sid, None)
                self._offsets.pop(sid, None)

        self._primed = True

    def _schedule_ai_digests(self) -> None:
        """Enqueue yesterday's daily digest (and, on Mondays, last week's retro)
        when enabled and not already generated/pending. Runs in a worker thread."""
        from .config import get_settings

        if not get_settings().ai_auto_digest:
            return
        svc = self.service
        if not svc.ai_runner.available():
            return
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        journal = svc.get_journal(yesterday)
        has_brief = any(
            n.kind == "brief" and n.author == "ai" and n.session_id is None
            for n in journal["notes"]
        )
        if (
            journal["sessions"]
            and not has_brief
            and not svc.ai_jobs.has_pending("daily_digest", {"day": yesterday})
        ):
            svc.enqueue_daily_digest(yesterday)
        # Monday: draft the retro for the week that just ended.
        today = datetime.now()
        if today.weekday() == 0:
            week_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            title = f"Weekly retro — week of {week_start}"
            existing = any(i.title == title for i in svc.list_investigations())
            if not existing and not svc.ai_jobs.has_pending(
                "weekly_retro", {"week_start": week_start}
            ):
                svc.enqueue_weekly_retro(week_start)

    async def _fire(self, cfg, rules, summary, kind: str, message: str, detail: str) -> None:
        delivered, deliver_detail = False, "notifications disabled"
        if cfg.enabled and cfg.topic.strip():
            click = f"http://{_host()}/sessions/{summary.session_id}"
            res = await asyncio.to_thread(
                self.service.send_notification,
                detail or message,
                title=message,
                click=click,
                tags="warning" if kind == "error" else "bell",
                priority=4 if kind == "error" else None,
            )
            delivered, deliver_detail = res.ok, res.detail
        self._log.append(
            AlertEvent(
                ts=datetime.now(timezone.utc),
                session_id=summary.session_id,
                title=summary.title,
                kind=kind,
                message=message,
                delivered=delivered,
                detail=deliver_detail,
            )
        )


def _host() -> str:
    from .config import get_settings

    s = get_settings()
    return f"{s.host}:{s.port}"
