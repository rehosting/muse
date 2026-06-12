"""Respond-from-muse: user-initiated interaction with a LIVE session's tmux pane.

All writes go through tmux only (the same transport autopilot already uses);
provider transcript dirs stay strictly read-only. The pane is re-discovered at
send time — a board card's pane id can be seconds stale — and failures return
400 with a precise reason so the UI can explain itself.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..autopilot import sessions as live_discovery
from ..autopilot import tmux

router = APIRouter(prefix="/api", tags=["interact"])

# Whitelist only: arbitrary keys into a live session are destructive, and
# permission-dialog automation (arrows/numbers) is deliberately NOT supported —
# the dialog state isn't reliably observable from outside.
_KEYS = {"escape": "Escape", "enter": "Enter"}


class RespondRequest(BaseModel):
    text: str
    submit: bool = True


class KeyRequest(BaseModel):
    key: str  # escape | enter | accept


def _find_pane(session_id: str) -> str:
    ls = next(
        (s for s in live_discovery.discover() if s.session_id == session_id), None
    )
    if ls is None:
        raise HTTPException(
            status_code=400,
            detail="session has no live process (not running, or not a Claude Code session)",
        )
    if not ls.pane_id:
        raise HTTPException(
            status_code=400,
            detail="process found but no tmux pane matched — session isn't running inside tmux",
        )
    return ls.pane_id


@router.post("/sessions/{session_id}/respond")
def respond(session_id: str, body: RespondRequest, request: Request) -> dict:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    pane = _find_pane(session_id)
    ok, err = tmux.send_text(pane, text, submit=body.submit)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log(
        session_id, "user_send", f"{pane} ← {text[:80]}"
    )
    return {"ok": True, "pane_id": pane}


@router.post("/sessions/{session_id}/keys")
def send_key(session_id: str, body: KeyRequest, request: Request) -> dict:
    pane = _find_pane(session_id)
    if body.key == "accept":
        ok, err = tmux.accept_suggestion(pane)
    elif body.key in _KEYS:
        ok, err = tmux.send_key(pane, _KEYS[body.key])
    else:
        raise HTTPException(status_code=400, detail=f"key not allowed: {body.key!r}")
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log(session_id, "user_key", f"{pane} ← {body.key}")
    return {"ok": True}


@router.get("/sessions/{session_id}/sends")
def sends(session_id: str, request: Request, limit: int = 20) -> list[dict]:
    entries = request.app.state.autopilot.store.recent_log_for(session_id, limit)
    return [
        {"ts": e.ts.isoformat(), "action": e.action, "detail": e.detail}
        for e in entries
    ]


@router.get("/sessions/{session_id}/terminal")
def terminal(session_id: str, lines: int = 30) -> dict:
    pane = _find_pane(session_id)
    text = tmux.capture_pane(pane, max(5, min(lines, 200)))
    if not text:
        return {"ok": False, "text": "", "error": "capture-pane returned nothing"}
    return {"ok": True, "text": text}
