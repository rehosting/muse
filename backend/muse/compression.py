"""Content-type-aware gzip — pure ASGI, SSE-safe.

Big thread payloads are the remaining "loading is slow" cost over a port-forward:
the 45MB live session serializes to a ~11MB JSON body that gzips to ~2.3MB (4.7x).
Starlette's stock GZipMiddleware compresses *every* content type, including
`text/event-stream`, which buffers and stalls our live streams — the exact spin
class we just eliminated (see ..sse). This middleware instead:

  * passes `text/event-stream` (and already-encoded) responses through untouched,
    streaming chunk-for-chunk with no buffering, and
  * buffers + gzips only ordinary (already-complete) responses above a threshold.

Compression runs in a thread executor: zlib releases the GIL, so a 300ms compress
of an 11MB body doesn't block the event loop / starve concurrent requests.
"""

from __future__ import annotations

import asyncio
import gzip
from typing import Iterable

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Below this, the gzip header overhead + executor hop isn't worth it.
MINIMUM_SIZE = 1024
COMPRESS_LEVEL = 6


def _accepts_gzip(scope: Scope) -> bool:
    for key, value in scope.get("headers", []):
        if key == b"accept-encoding":
            return "gzip" in value.decode("latin-1").lower()
    return False


class GzipBufferedMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = MINIMUM_SIZE) -> None:
        self.app = app
        self.minimum_size = minimum_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _accepts_gzip(scope):
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        body = bytearray()
        passthrough = False

        async def wrapped_send(message: Message) -> None:
            nonlocal start, passthrough
            mtype = message["type"]

            if mtype == "http.response.start":
                headers = Headers(raw=message["headers"])
                content_type = headers.get("content-type", "")
                # Never touch live streams or already-compressed bodies: forward the
                # start now and let the body stream through unchanged.
                if headers.get("content-encoding") or content_type.startswith(
                    "text/event-stream"
                ):
                    passthrough = True
                    await send(message)
                else:
                    start = message  # hold until we've seen the full body
                return

            if mtype == "http.response.body":
                if passthrough:
                    await send(message)
                    return
                body.extend(message.get("body", b""))
                if message.get("more_body", False):
                    return  # keep buffering
                await self._flush(send, start, bytes(body))
                return

            await send(message)

        await self.app(scope, receive, wrapped_send)

    async def _flush(self, send: Send, start: Message | None, payload: bytes) -> None:
        assert start is not None
        if len(payload) < self.minimum_size:
            await send(start)
            await send({"type": "http.response.body", "body": payload, "more_body": False})
            return

        loop = asyncio.get_event_loop()
        compressed = await loop.run_in_executor(
            None, _gzip, payload, COMPRESS_LEVEL
        )

        headers = MutableHeaders(raw=list(start["headers"]))
        headers["content-encoding"] = "gzip"
        headers["content-length"] = str(len(compressed))
        headers["vary"] = _merge_vary(headers.get("vary"))
        start["headers"] = headers.raw

        await send(start)
        await send({"type": "http.response.body", "body": compressed, "more_body": False})


def _gzip(payload: bytes, level: int) -> bytes:
    return gzip.compress(payload, compresslevel=level)


def _merge_vary(existing: str | None) -> str:
    if not existing:
        return "Accept-Encoding"
    parts: Iterable[str] = (p.strip() for p in existing.split(","))
    if any(p.lower() == "accept-encoding" for p in parts):
        return existing
    return f"{existing}, Accept-Encoding"
