"""gzip middleware invariants: large JSON is compressed, but text/event-stream
is passed through untouched (compressing SSE buffers/stalls live streams — the
spin class we eliminated). The SSE-passthrough test is the regression guard
against anyone swapping in Starlette's stock GZipMiddleware."""

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from muse.compression import MINIMUM_SIZE, GzipBufferedMiddleware

BIG = "x" * (MINIMUM_SIZE * 4)


def _client():
    async def big(request):
        return PlainTextResponse(BIG)

    async def tiny(request):
        return PlainTextResponse("hi")

    async def stream(request):
        async def gen():
            yield b"event: hello\ndata: " + b"y" * (MINIMUM_SIZE * 4) + b"\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[
        Route("/big", big),
        Route("/tiny", tiny),
        Route("/stream", stream),
    ])
    app.add_middleware(GzipBufferedMiddleware)
    return TestClient(app)


def test_large_response_is_gzipped():
    r = _client().get("/big", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in r.headers["vary"].lower()
    # httpx transparently decompresses, so the body is the original.
    assert r.text == BIG


def test_no_accept_encoding_is_untouched():
    # TestClient/httpx sends Accept-Encoding by default; strip it explicitly.
    r = _client().get("/big", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
    assert r.text == BIG


def test_small_response_not_gzipped():
    r = _client().get("/tiny", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in r.headers


def test_event_stream_is_never_gzipped():
    with _client().stream("GET", "/stream", headers={"Accept-Encoding": "gzip"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in r.headers
        body = b"".join(r.iter_raw())
    # Raw bytes are the SSE frame, NOT a gzip member.
    assert body.startswith(b"event: hello")
    assert body[:2] != b"\x1f\x8b"  # gzip magic
