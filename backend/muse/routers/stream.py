"""SSE endpoint for live-tailing a session."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request

from ..sse import RawSSE

router = APIRouter(prefix="/api", tags=["stream"])

HEARTBEAT_SECONDS = 15


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    service = request.app.state.service
    queue = await service.subscribe(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="session not found")

    async def event_generator():
        # No disconnect polling here: RawSSE streams with plain ASGI sends (no anyio
        # task group / no receive loop), so a gone client just lingers idle until
        # the bounded lifetime ends — it cannot spin the event loop (see ..sse).
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                yield {"event": event.type, "data": json.dumps(event.data)}
        finally:
            await service.unsubscribe(session_id, queue)

    return RawSSE(event_generator())
