"""REST endpoints for Investigations (AI/user markup documents).

These back the muse web UI (list/detail + session backlinks) and let the user
curate what their Claude Code created over MCP. All state is muse-owned
(~/.muse/muse.db); transcripts stay read-only.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import (
    Investigation,
    InvestigationRef,
    InvestigationSummary,
    SessionBacklink,
)

router = APIRouter(prefix="/api", tags=["investigations"])


def _service(request: Request):
    return request.app.state.service


class RefInput(BaseModel):
    session_id: str
    anchor_uuid: Optional[str] = None
    label: str = ""
    comment: str = ""


class InvestigationCreate(BaseModel):
    title: str
    body: str = ""
    author: str = "user"  # REST default is user; MCP tools pass author="ai"
    status: str = "open"
    refs: list[RefInput] = []


class InvestigationUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    status: Optional[str] = None
    append_body: Optional[str] = None


@router.get("/investigations", response_model=list[InvestigationSummary])
def list_investigations(request: Request) -> list[InvestigationSummary]:
    return _service(request).list_investigations()


@router.post("/investigations", response_model=Investigation)
def create_investigation(body: InvestigationCreate, request: Request) -> Investigation:
    refs = [r.model_dump() for r in body.refs]
    try:
        return _service(request).create_investigation(
            body.title, body.body, body.author, body.status, refs
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/investigations/{investigation_id}", response_model=Investigation)
def get_investigation(investigation_id: str, request: Request) -> Investigation:
    inv = _service(request).get_investigation(investigation_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="investigation not found")
    return inv


@router.put("/investigations/{investigation_id}", response_model=Investigation)
def update_investigation(
    investigation_id: str, body: InvestigationUpdate, request: Request
) -> Investigation:
    inv = _service(request).update_investigation(
        investigation_id, body.title, body.body, body.status, body.append_body
    )
    if inv is None:
        raise HTTPException(status_code=404, detail="investigation not found")
    return inv


@router.delete("/investigations/{investigation_id}")
def delete_investigation(investigation_id: str, request: Request) -> dict:
    ok = _service(request).delete_investigation(investigation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="investigation not found")
    return {"ok": True}


@router.post("/investigations/{investigation_id}/refs", response_model=InvestigationRef)
def add_reference(
    investigation_id: str, body: RefInput, request: Request
) -> InvestigationRef:
    try:
        ref = _service(request).add_reference(
            investigation_id, body.session_id, body.anchor_uuid, body.label, body.comment
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if ref is None:
        raise HTTPException(status_code=404, detail="investigation not found")
    return ref


@router.delete("/investigations/{investigation_id}/refs/{ref_id}")
def remove_reference(investigation_id: str, ref_id: str, request: Request) -> dict:
    ok = _service(request).remove_reference(ref_id)
    if not ok:
        raise HTTPException(status_code=404, detail="reference not found")
    return {"ok": True}


@router.get("/sessions/{session_id}/references", response_model=list[SessionBacklink])
def get_session_references(session_id: str, request: Request) -> list[SessionBacklink]:
    return _service(request).get_session_references(session_id)
