"""SSE endpoint for live-tailing a session."""

from __future__ import annotations

import asyncio
import os
import time

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api", tags=["stream"])

HEARTBEAT_SECONDS = 15
# Hard cap on a single SSE stream's life. EventSource reconnects transparently,
# so this is invisible to the client — but it guarantees a connection a proxy
# left half-closed (which uvicorn won't reap while a response is in flight, and
# which makes the event loop spin) is torn down within this window instead of
# lingering for hours. Tunable; 0 disables.
SSE_MAX_SECONDS = int(os.environ.get("MUSE_SSE_MAX_SECONDS", "300"))


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    service = request.app.state.service
    queue = await service.subscribe(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="session not found")

    async def event_generator():
        # NOTE: do NOT poll `request.is_disconnected()` here. It consumes the ASGI
        # receive channel that sse_starlette's own disconnect listener needs, so
        # the http.disconnect is stolen, the stream is never cancelled, and the
        # `finally` cleanup never runs — leaking the subscription AND the
        # per-session tailer (which force-polls the FS every 500ms) forever. That
        # leak is what pegged the event loop to 100% over time. sse_starlette
        # cancels this generator on disconnect; `ping=` also catches half-open
        # sockets via a failed write.
        deadline = time.monotonic() + SSE_MAX_SECONDS if SSE_MAX_SECONDS else None
        try:
            while True:
                if deadline and time.monotonic() > deadline:
                    break  # bounded lifetime — client reconnects, half-open conns get reaped
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                import json

                yield {"event": event.type, "data": json.dumps(event.data)}
        finally:
            await service.unsubscribe(session_id, queue)

    return EventSourceResponse(event_generator(), ping=HEARTBEAT_SECONDS)
