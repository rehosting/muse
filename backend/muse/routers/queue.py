"""Queued replies: leave a message for a BUSY session; muse types it into the
session's pane when the turn actually ends (idle, no menu, no rate-limit banner).

This is the "send when ready" counterpart to interact.py's immediate send. The
delivery itself lives in the autopilot controller (the one disciplined automated
tmux-write site) — these endpoints are just the user-facing queue bookkeeping.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import QueuedReply, QueueView

router = APIRouter(prefix="/api", tags=["queue"])

_MAX_TEXT = 4000


class QueueRequest(BaseModel):
    text: str
    mode: str = "turn"  # turn = own turn; append = glue onto the previous item


class QueueModeRequest(BaseModel):
    mode: str  # turn | append


@router.get("/sessions/{session_id}/queue")
def get_queue(session_id: str, request: Request) -> QueueView:
    ap = request.app.state.autopilot
    return QueueView(
        items=ap.store.queue_for(session_id),
        hold_reason=ap.queue_hold_reason(session_id),
    )


@router.post("/sessions/{session_id}/queue")
def add_to_queue(session_id: str, body: QueueRequest, request: Request) -> QueuedReply:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > _MAX_TEXT:
        raise HTTPException(status_code=400, detail=f"text too long (max {_MAX_TEXT})")
    if body.mode not in ("turn", "append"):
        raise HTTPException(status_code=400, detail=f"unknown mode: {body.mode!r}")
    ap = request.app.state.autopilot
    item = ap.store.queue_add(session_id, text, mode=body.mode)
    ap.store.log(session_id, "queue_add", f"#{item.id} {text[:80]}")
    return item


@router.post("/sessions/{session_id}/queue/{qid}/mode")
def set_queued_mode(
    session_id: str, qid: int, body: QueueModeRequest, request: Request
) -> dict:
    """Flip a pending item between 'turn' (its own turn) and 'append' (glue onto
    the previous item so both go in one message)."""
    if body.mode not in ("turn", "append"):
        raise HTTPException(status_code=400, detail=f"unknown mode: {body.mode!r}")
    ap = request.app.state.autopilot
    if not ap.store.queue_set_mode(session_id, qid, body.mode):
        raise HTTPException(status_code=404, detail="not pending (already sent or cancelled)")
    return {"ok": True, "mode": body.mode}


@router.post("/sessions/{session_id}/queue/send-now")
def send_queued_now(session_id: str, request: Request) -> dict:
    """User override: deliver the next queued batch immediately, skipping the
    wait-for-idle gate. Still refuses to type over an open menu / limit banner."""
    ap = request.app.state.autopilot
    sent, reason = ap.deliver_now(session_id)
    if not sent:
        raise HTTPException(status_code=409, detail=reason or "nothing to send")
    ap.store.log(session_id, "queue_send_now", f"{sent}")
    return {"ok": True, "sent": sent}


@router.delete("/sessions/{session_id}/queue/{qid}")
def cancel_queued(session_id: str, qid: int, request: Request) -> dict:
    ap = request.app.state.autopilot
    if not ap.store.queue_cancel(session_id, qid):
        raise HTTPException(status_code=404, detail="not pending (already sent or cancelled)")
    ap.store.log(session_id, "queue_cancel", f"#{qid}")
    return {"ok": True}
