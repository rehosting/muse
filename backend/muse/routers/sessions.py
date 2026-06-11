"""REST endpoints for sessions, threads, subagents, and persisted output."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..models import (
    Annotations,
    Bookmark,
    FileChange,
    SearchResponse,
    SessionEvent,
    SessionLineage,
    SessionSummary,
    StatsResponse,
    SubagentUsage,
    Thread,
    TokenUsage,
    UsageAtAnchor,
    UsageTimeline,
)


class TitleUpdate(BaseModel):
    title: str | None = None


class BookmarkUpdate(BaseModel):
    note: str = ""

router = APIRouter(prefix="/api", tags=["sessions"])


def _service(request: Request):
    return request.app.state.service


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(request: Request) -> list[SessionSummary]:
    return _service(request).list_sessions()


@router.get("/stats", response_model=StatsResponse)
def get_stats(request: Request) -> StatsResponse:
    return _service(request).get_stats()


@router.get("/search", response_model=SearchResponse)
def search(
    request: Request,
    q: str = Query("", description="full-text query"),
    limit: int = Query(30, ge=1, le=100),
) -> SearchResponse:
    svc = _service(request)
    if not q.strip():
        # Cheap status for the palette's "N sessions indexed" footer on open.
        return SearchResponse(
            query=q,
            indexed_sessions=svc.search_index.indexed_sessions(),
            available=svc.search_index.available,
        )
    return svc.search(q, limit)


@router.get("/sessions/{session_id}", response_model=Thread)
def get_thread(session_id: str, request: Request) -> Thread:
    thread = _service(request).get_thread(session_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="session not found")
    return thread


@router.get("/sessions/{session_id}/subagents/{agent_id}", response_model=Thread)
def get_subagent(session_id: str, agent_id: str, request: Request) -> Thread:
    thread = _service(request).get_subagent(session_id, agent_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="subagent not found")
    return thread


@router.get("/sessions/{session_id}/events", response_model=list[SessionEvent])
def get_events(session_id: str, request: Request) -> list[SessionEvent]:
    events = _service(request).get_events(session_id)
    if events is None:
        raise HTTPException(status_code=404, detail="session not found")
    return events


@router.get(
    "/sessions/{session_id}/subagents/{agent_id}/events",
    response_model=list[SessionEvent],
)
def get_subagent_events(
    session_id: str, agent_id: str, request: Request
) -> list[SessionEvent]:
    events = _service(request).get_events(session_id, agent_id)
    if events is None:
        raise HTTPException(status_code=404, detail="subagent not found")
    return events


@router.get("/sessions/{session_id}/files", response_model=list[FileChange])
def get_files(session_id: str, request: Request) -> list[FileChange]:
    files = _service(request).get_file_changes(session_id)
    if files is None:
        raise HTTPException(status_code=404, detail="session not found")
    return files


@router.get(
    "/sessions/{session_id}/subagents/{agent_id}/files",
    response_model=list[FileChange],
)
def get_subagent_files(
    session_id: str, agent_id: str, request: Request
) -> list[FileChange]:
    files = _service(request).get_file_changes(session_id, agent_id)
    if files is None:
        raise HTTPException(status_code=404, detail="subagent not found")
    return files


@router.get("/sessions/{session_id}/lineage", response_model=SessionLineage)
def get_lineage(session_id: str, request: Request) -> SessionLineage:
    lin = _service(request).get_lineage(session_id)
    if lin is None:
        raise HTTPException(status_code=404, detail="session not found")
    return lin


@router.get("/sessions/{session_id}/tokens", response_model=TokenUsage)
def get_session_tokens(session_id: str, request: Request) -> TokenUsage:
    usage = _service(request).get_session_tokens(session_id)
    if usage is None:
        raise HTTPException(status_code=404, detail="session not found")
    return usage


@router.get("/sessions/{session_id}/usage/timeline", response_model=UsageTimeline)
def get_usage_timeline(
    session_id: str, request: Request, limit: int = Query(100, ge=1, le=1000)
) -> UsageTimeline:
    tl = _service(request).get_usage_timeline(session_id, limit)
    if tl is None:
        raise HTTPException(status_code=404, detail="timeline unavailable for this session")
    return tl


@router.get("/sessions/{session_id}/usage/at/{anchor_uuid}", response_model=UsageAtAnchor)
def get_usage_at_anchor(session_id: str, anchor_uuid: str, request: Request) -> UsageAtAnchor:
    res = _service(request).get_usage_at_anchor(session_id, anchor_uuid)
    if res is None:
        raise HTTPException(status_code=404, detail="usage-at-anchor unavailable for this session")
    return res


@router.get("/sessions/{session_id}/subagents", response_model=list[SubagentUsage])
def list_subagents(session_id: str, request: Request) -> list[SubagentUsage]:
    subs = _service(request).list_subagents(session_id)
    if subs is None:
        raise HTTPException(status_code=404, detail="subagent accounting unavailable")
    return subs


@router.get("/sessions/{session_id}/annotations", response_model=Annotations)
def get_annotations(session_id: str, request: Request) -> Annotations:
    return _service(request).get_annotations(session_id)


@router.put("/sessions/{session_id}/title", response_model=Annotations)
def set_title(session_id: str, body: TitleUpdate, request: Request) -> Annotations:
    return _service(request).set_title(session_id, body.title)


@router.put("/sessions/{session_id}/bookmarks/{message_uuid}", response_model=Bookmark)
def upsert_bookmark(
    session_id: str, message_uuid: str, body: BookmarkUpdate, request: Request
) -> Bookmark:
    return _service(request).upsert_bookmark(session_id, message_uuid, body.note)


@router.delete("/sessions/{session_id}/bookmarks/{message_uuid}")
def delete_bookmark(session_id: str, message_uuid: str, request: Request) -> dict:
    _service(request).delete_bookmark(session_id, message_uuid)
    return {"ok": True}


@router.get("/sessions/{session_id}/tool-results/{cache_id}")
def get_tool_result(
    session_id: str,
    cache_id: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1),
) -> dict:
    result = _service(request).get_persisted_output(session_id, cache_id, offset, limit)
    if result is None:
        raise HTTPException(status_code=404, detail="tool result not found")
    return result
