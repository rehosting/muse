"""SSE endpoint for live-tailing a session."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api", tags=["stream"])

HEARTBEAT_SECONDS = 15


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    service = request.app.state.service
    queue = await service.subscribe(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="session not found")

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                import json

                yield {"event": event.type, "data": json.dumps(event.data)}
        finally:
            await service.unsubscribe(session_id, queue)

    return EventSourceResponse(event_generator())
