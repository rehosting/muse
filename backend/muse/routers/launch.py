"""REST endpoints for context packs + the session launcher.

A pack is hand-off markdown under ~/.muse/packs/ (muse-owned); launch opens a
new tmux window running `claude <seed prompt>` in the chosen project cwd, and
always returns the equivalent shell command for clipboard fallback.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import Pack

router = APIRouter(prefix="/api", tags=["launch"])


def _service(request: Request):
    return request.app.state.service


class PackCreate(BaseModel):
    source_session_id: Optional[str] = None
    include_brief: bool = True
    note_ids: list[str] = []
    include_files: bool = True
    extra_md: str = ""
    title: str = ""


class LaunchRequest(BaseModel):
    cwd: str
    prompt: str = ""
    pack_id: Optional[str] = None


@router.post("/packs", response_model=Pack)
def create_pack(body: PackCreate, request: Request) -> Pack:
    return _service(request).create_pack(
        body.source_session_id, body.include_brief, body.note_ids,
        body.include_files, body.extra_md, body.title,
    )


@router.get("/packs", response_model=list[Pack])
def list_packs(request: Request) -> list[Pack]:
    return _service(request).packs.list()


@router.delete("/packs/{pack_id}")
def delete_pack(pack_id: str, request: Request) -> dict:
    if not _service(request).packs.delete(pack_id):
        raise HTTPException(status_code=404, detail="pack not found")
    return {"ok": True}


@router.post("/launch")
def launch(body: LaunchRequest, request: Request) -> dict:
    if not body.cwd.strip():
        raise HTTPException(status_code=400, detail="cwd is required")
    return _service(request).launch_session(body.cwd.strip(), body.prompt, body.pack_id)


@router.get("/launch/targets")
def launch_targets(request: Request) -> list[str]:
    return _service(request).launch_targets()
