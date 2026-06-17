"""Raw-ASGI SSE response — no anyio task group, no receive() disconnect loop.

THE muse "everything is slow" pathology, root-caused 2026-06-17 via
/api/debug/loop: the event loop pegged at 100% CPU with the ready queue full of
`anyio CancelScope._deliver_cancellation`. anyio re-schedules that callback via
`call_soon` for as long as a cancelled task group still has a child task that
hasn't exited. sse_starlette's EventSourceResponse (and Starlette's
StreamingResponse) run the stream + a `while: await receive()` disconnect listener
inside `anyio.create_task_group()`. On the connection states a reverse proxy
leaves behind, a child never settles, so anyio busy-loops trying to cancel it —
one stuck scope per orphaned SSE connection — GIL-starving every request (a
session open went from 0.5s to 60s+).

This streams with plain ASGI sends: no task group, no concurrent receive listener,
so there is nothing for anyio to busy-loop cancelling. A disconnected client's
response just lingers idle (zero CPU — it awaits the next event/heartbeat) until
its bounded lifetime expires; uvicorn's send() no-ops once the peer is gone. The
client's EventSource reconnects transparently across that boundary.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

# Bounded stream lifetime: a disconnected/half-open connection self-closes within
# this window (client reconnects transparently). Short enough that orphaned
# subscriptions don't accumulate; long enough that reconnects are rare. 0 disables.
SSE_MAX_SECONDS = int(os.environ.get("MUSE_SSE_MAX_SECONDS", "90"))


def _format(item: dict) -> bytes:
    """Encode {"event":.., "data":..} as an SSE frame."""
    parts: list[str] = []
    event = item.get("event")
    if event:
        parts.append(f"event: {event}")
    for line in str(item.get("data", "")).split("\n"):
        parts.append(f"data: {line}")
    return ("\n".join(parts) + "\n\n").encode("utf-8")


class RawSSE(Response):
    """Stream an async iterator of {"event","data"} dicts as text/event-stream.

    Subclasses Response so FastAPI/Starlette invoke it as an ASGI app (calls
    __call__), but the body is produced by streaming the generator, not by the
    Response body machinery.
    """

    media_type = "text/event-stream"

    def __init__(self, gen: AsyncIterator[dict], *, max_seconds: int | None = None):
        self.gen = gen
        self.max_seconds = SSE_MAX_SECONDS if max_seconds is None else max_seconds
        super().__init__(content=b"", media_type="text/event-stream")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache"),
                (b"connection", b"keep-alive"),
                (b"x-accel-buffering", b"no"),  # don't let nginx/proxies buffer SSE
            ],
        })
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.max_seconds if self.max_seconds else None
        try:
            async for item in self.gen:
                await send({
                    "type": "http.response.body",
                    "body": _format(item),
                    "more_body": True,
                })
                if deadline and loop.time() > deadline:
                    break
        finally:
            # Run the generator's own finally (unsubscribe / release) even on break.
            await self.gen.aclose()
        await send({"type": "http.response.body", "body": b"", "more_body": False})
