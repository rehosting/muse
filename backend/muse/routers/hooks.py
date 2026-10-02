"""Ingest Claude Code hook events (the relay installed by `muse hooks install`).

Claude Code pipes each lifecycle event's JSON to ~/.muse/hook.sh, which POSTs it
here. This turns muse's picture of the fleet from "polled every few seconds"
into "told the instant something happens":

- Stop            → deliver the next queued reply for that session immediately;
                    if nothing was queued, push a (settled) "ready for you" alert.
- Notification    → push "needs your permission / input" immediately.
- UserPromptSubmit→ the user replied — cancel any in-flight turn-ended alert.
- SessionEnd      → push "session ended" (when that alert rule is on).

The handler itself must return FAST (Claude Code waits on the relay, which waits
on curl): all real work runs as a background task on the event loop. The polling
watcher stays as the fallback for sessions without hooks; suppression windows in
AlertsWatcher keep the two sources from double-firing.
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from .. import hooksetup

router = APIRouter(prefix="/api", tags=["hooks"])

_SID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
# Small pause after Stop before typing a queued reply: the TUI needs a beat to
# repaint its prompt after the turn ends.
_QUEUE_DELIVER_DELAY = 1.0


def _ring(request: Request) -> deque:
    ring = getattr(request.app.state, "hook_events", None)
    if ring is None:
        ring = request.app.state.hook_events = deque(maxlen=200)
    return ring


async def _dispatch(state, event: str, sid: str, payload: dict) -> None:
    alerts = state.alerts
    if event == "Stop":
        await asyncio.sleep(_QUEUE_DELIVER_DELAY)
        delivered = await asyncio.to_thread(state.autopilot.deliver_queued, sid)
        if delivered:
            # The queued reply is the user's answer — no "ready for you" push,
            # and any in-flight settle alert is superseded.
            alerts.hook_user_replied(sid)
        else:
            await alerts.hook_turn_ended(sid)
    elif event == "Notification":
        await alerts.hook_needs_you(sid, str(payload.get("message") or ""))
    elif event == "UserPromptSubmit":
        alerts.hook_user_replied(sid)
    elif event == "SessionEnd":
        await alerts.hook_session_end(sid)
    # Other events (SubagentStop, PreCompact, …) are recorded but need no action.


@router.post("/hooks/claude")
async def claude_hook(request: Request) -> dict:
    """Accept one Claude Code hook payload. Never errors on bad input — the
    relay must always succeed from Claude Code's point of view."""
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "detail": "unparseable payload"}
    if not isinstance(payload, dict):
        return {"ok": False, "detail": "not an object"}
    event = str(payload.get("hook_event_name") or "")
    sid = str(payload.get("session_id") or "")
    _ring(request).appendleft(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "session_id": sid,
            "message": str(payload.get("message") or "")[:200],
        }
    )
    if event and _SID_RE.match(sid):
        # Fire-and-forget: the relay's curl has a 3s timeout and Claude Code is
        # waiting on it — the settle delays alone exceed that.
        asyncio.get_running_loop().create_task(
            _dispatch(request.app.state, event, sid, payload)
        )
    return {"ok": True}


@router.get("/hooks/status")
def hooks_status(request: Request) -> dict:
    """Install state + live ingest counters (the AlertsPage 'instant telemetry'
    card and `muse hooks status` both read this)."""
    ring = _ring(request)
    by_event: dict[str, int] = {}
    for e in ring:
        by_event[e["event"]] = by_event.get(e["event"], 0) + 1
    return {
        **hooksetup.status(),
        "events_seen": len(ring),
        "by_event": by_event,
        "last_event_at": ring[0]["ts"] if ring else None,
        "recent": list(ring)[:20],
    }
