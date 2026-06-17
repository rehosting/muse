"""Mission-control board endpoints: cached snapshot + one multiplexed SSE.

GET /api/board returns the ticker's latest snapshot (zero compute beyond the
cold first build) — it doubles as the SSE bootstrap and the polling fallback.
GET /api/board/stream pushes `snapshot` once, then `cards` deltas, with the
same heartbeat discipline as the per-session stream.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import APIRouter, Request

from ..models import BoardSnapshot
from ..sse import SafeEventSourceResponse

router = APIRouter(prefix="/api", tags=["board"])

HEARTBEAT_SECONDS = 15
# See stream.py: bound the stream's life so a proxy-half-closed connection can't
# linger (and spin the event loop) for hours. EventSource reconnects silently.
SSE_MAX_SECONDS = int(os.environ.get("MUSE_SSE_MAX_SECONDS", "300"))


@router.get("/board", response_model=BoardSnapshot)
async def get_board(request: Request) -> BoardSnapshot:
    return await request.app.state.board.get_snapshot()


@router.get("/board/stream")
async def stream_board(request: Request):
    board = request.app.state.board
    broker = request.app.state.broker
    snapshot = await board.get_snapshot()
    await board.acquire()
    queue = await broker.subscribe("board")

    async def event_generator():
        # See stream.py: polling request.is_disconnected() here steals the ASGI
        # receive channel from sse_starlette's disconnect listener, so the stream
        # is never cancelled on disconnect and `finally` never releases the board
        # ticker / subscription — a leak that pegs the event loop over time.
        deadline = time.monotonic() + SSE_MAX_SECONDS if SSE_MAX_SECONDS else None
        try:
            yield {"event": "snapshot", "data": snapshot.model_dump_json()}
            while True:
                if deadline and time.monotonic() > deadline:
                    break  # bounded lifetime — client reconnects, half-open conns get reaped
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                yield {"event": event.type, "data": json.dumps(event.data)}
        finally:
            await broker.unsubscribe("board", queue)
            await board.release()

    return SafeEventSourceResponse(event_generator(), ping=HEARTBEAT_SECONDS)
