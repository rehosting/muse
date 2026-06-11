"""REST endpoints for worklog notes and the daily journal.

Lightweight running notes about active work (muse-owned, ~/.muse/muse.db).
The journal view interleaves a day's notes with the sessions active that day.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import Note

router = APIRouter(prefix="/api", tags=["worklog"])


def _service(request: Request):
    return request.app.state.service


class NoteCreate(BaseModel):
    body: str
    session_id: Optional[str] = None
    anchor_uuid: Optional[str] = None
    kind: str = "note"  # note | next | brief
    author: str = "user"  # REST default is user; MCP tools pass author="ai"


class NoteUpdate(BaseModel):
    body: Optional[str] = None
    kind: Optional[str] = None


@router.get("/notes", response_model=list[Note])
def list_notes(
    request: Request,
    session_id: Optional[str] = None,
    day: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 200,
) -> list[Note]:
    return _service(request).list_notes(session_id, day, kind, limit)


@router.post("/notes", response_model=Note)
def create_note(body: NoteCreate, request: Request) -> Note:
    try:
        return _service(request).create_note(
            body.body, body.session_id, body.anchor_uuid, body.kind, body.author
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/notes/{note_id}", response_model=Note)
def update_note(note_id: str, body: NoteUpdate, request: Request) -> Note:
    note = _service(request).update_note(note_id, body.body, body.kind)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return note


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, request: Request) -> dict:
    ok = _service(request).delete_note(note_id)
    if not ok:
        raise HTTPException(status_code=404, detail="note not found")
    return {"ok": True}


@router.get("/journal/{day}")
def get_journal(day: str, request: Request) -> dict:
    return _service(request).get_journal(day)


@router.get("/open-loops")
def get_open_loops(request: Request) -> list[dict]:
    """Recently-active-but-unfinished sessions for the continue-working rail."""
    return _service(request).get_open_loops()
