"""Tests for the pure-ASGI auth middleware: the 401/200 matrix, loopback bypass,
SSE short-circuit, and the no-token-configured regression guard."""

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from muse.auth import COOKIE_NAME, AuthMiddleware, load_or_create_token

TOKEN = "s3cret-token"


def _build(token, allow_loopback=True, client=("9.9.9.9", 5000)):
    async def data(request):
        return JSONResponse({"ok": True})

    async def stream(request):
        async def gen():
            yield b"event: hello\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    async def login(request):
        return JSONResponse({"login": True})

    app = Starlette(routes=[
        Route("/api/sessions", data),
        Route("/api/board/stream", stream),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/auth/status", data),
        Route("/", data),
        Route("/assets/x.js", data),
        Route("/mcp/", data, methods=["GET", "POST"]),
    ])
    app.add_middleware(
        AuthMiddleware, token_provider=lambda: token, allow_loopback=allow_loopback
    )
    return TestClient(app, client=client)


# --- matrix (token configured, loopback bypass on) -----------------------------

def test_remote_no_credential_401():
    c = _build(TOKEN)
    assert c.get("/api/sessions").status_code == 401


def test_remote_bearer_good_200():
    c = _build(TOKEN)
    r = c.get("/api/sessions", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_remote_bearer_bad_401():
    c = _build(TOKEN)
    r = c.get("/api/sessions", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_remote_cookie_good_200():
    c = _build(TOKEN)
    r = c.get("/api/sessions", headers={"Cookie": f"{COOKIE_NAME}={TOKEN}"})
    assert r.status_code == 200


def test_loopback_bypasses():
    c = _build(TOKEN, client=("127.0.0.1", 1234))
    assert c.get("/api/sessions").status_code == 200


def test_loopback_bypass_can_be_disabled():
    c = _build(TOKEN, allow_loopback=False, client=("127.0.0.1", 1234))
    assert c.get("/api/sessions").status_code == 401


def test_mcp_protected_remote():
    c = _build(TOKEN)
    assert c.post("/mcp/").status_code == 401
    assert c.post("/mcp/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_spa_and_assets_public():
    c = _build(TOKEN)
    assert c.get("/").status_code == 200
    assert c.get("/assets/x.js").status_code == 200


def test_auth_endpoints_exempt():
    c = _build(TOKEN)
    assert c.post("/api/auth/login", json={"token": "x"}).status_code == 200
    assert c.get("/api/auth/status").status_code == 200


def test_options_preflight_never_401():
    c = _build(TOKEN)
    # Starlette returns 405 for an unhandled OPTIONS, but crucially NOT 401 —
    # the middleware must let preflights through to CORS.
    assert c.options("/api/sessions").status_code != 401


def test_no_token_configured_is_permissive():
    c = _build(None)  # regression guard: today's local default
    assert c.get("/api/sessions").status_code == 200
    assert c.post("/mcp/").status_code == 200


# --- SSE short-circuits before any body bytes ----------------------------------

def test_sse_401_before_body():
    c = _build(TOKEN)
    r = c.get("/api/board/stream")
    assert r.status_code == 401
    assert b"hello" not in r.content  # generator never ran


def test_sse_streams_with_cookie():
    c = _build(TOKEN)
    r = c.get("/api/board/stream", headers={"Cookie": f"{COOKIE_NAME}={TOKEN}"})
    assert r.status_code == 200
    assert b"hello" in r.content


# --- token sourcing -------------------------------------------------------------

def test_load_token_env_wins(tmp_path):
    assert load_or_create_token("envtok", tmp_path, generate=True) == "envtok"


def test_load_token_generates_only_when_asked(tmp_path):
    assert load_or_create_token(None, tmp_path, generate=False) is None
    assert not (tmp_path / "auth_token").exists()
    tok = load_or_create_token(None, tmp_path, generate=True)
    assert tok and (tmp_path / "auth_token").read_text().strip() == tok
    # chmod 600
    assert oct((tmp_path / "auth_token").stat().st_mode)[-3:] == "600"


def test_load_token_reads_existing_file(tmp_path):
    (tmp_path / "auth_token").write_text("filetok\n")
    assert load_or_create_token(None, tmp_path, generate=False) == "filetok"
