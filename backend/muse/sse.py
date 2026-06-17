"""Spin-proof SSE response.

Both sse_starlette's EventSourceResponse and Starlette's StreamingResponse run a
disconnect listener shaped like `while active: msg = await receive()`. uvicorn's
`receive()` blocks on an idle request — UNLESS the connection has pending bytes or
is in certain keep-alive/proxy states, in which case it returns `http.request`
immediately and repeatedly. The listener ignores those and loops, so it busy-spins
the event loop at ~100% CPU (one spinning listener per orphaned SSE connection).
This was the muse "everything is slow" pathology: the spin GIL-starves every
request (a session open went from 0.5s to 60s+).

`SafeEventSourceResponse` keeps all of sse_starlette's behavior but wraps the
`receive` channel so non-disconnect messages are swallowed with a small sleep
floor. The listener then only ever wakes on a genuine `http.disconnect`, and can
never busy-loop no matter what uvicorn/the proxy feeds it.
"""

from __future__ import annotations

import asyncio

from sse_starlette.sse import EventSourceResponse
from starlette.types import Receive, Scope, Send

# Floor between receive() polls when a non-disconnect message arrives. Disconnect
# is still detected within this window; CPU stays negligible under a message flood.
_POLL_FLOOR_SECONDS = 0.5


class SafeEventSourceResponse(EventSourceResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def tamed_receive():
            # Only ever hand back http.disconnect; swallow http.request floods with
            # a sleep floor so the listener loop can't peg the CPU.
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return message
                await asyncio.sleep(_POLL_FLOOR_SECONDS)

        await super().__call__(scope, tamed_receive, send)
