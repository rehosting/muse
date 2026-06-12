"""Mission-control board endpoints: cached snapshot + one multiplexed SSE.

GET /api/board returns the ticker's latest snapshot (zero compute beyond the
cold first build) — it doubles as the SSE bootstrap and the polling fallback.
GET /api/board/stream pushes `snapshot` once, then `cards` deltas, with the
same heartbeat discipline as the per-session stream.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from ..models import BoardSnapshot

router = APIRouter(prefix="/api", tags=["board"])

HEARTBEAT_SECONDS = 15


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
        try:
            yield {"event": "snapshot", "data": snapshot.model_dump_json()}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
                    continue
                yield {"event": event.type, "data": json.dumps(event.data)}
        finally:
            await broker.unsubscribe("board", queue)
            await board.release()

    return EventSourceResponse(event_generator())
