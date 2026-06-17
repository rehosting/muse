"""Minimal single-user auth for remote access (tailscale / LAN binds).

PURE ASGI middleware — deliberately NOT Starlette's BaseHTTPMiddleware, whose
response-pumping layer buffers/loses streaming responses and breaks SSE (and
the MCP Streamable HTTP sub-app). This wrapper either short-circuits a 401 or
passes the scope through untouched, so it is streaming-safe by construction
and covers mounted sub-apps (/mcp) that per-router dependencies would miss.

Policy:
- Protected: /api/* and /mcp* (data + control). The SPA shell and /assets stay
  public — they're public code from a public repo; all data flows over /api.
- Accepted credentials: `Authorization: Bearer <token>`, the `muse_auth`
  cookie (HttpOnly, set by /api/auth/login — EventSource can't send headers,
  cookies ride along automatically), or a loopback client (default ON so the
  local UI, vite dev proxy, scripts, and local Claude Code MCP keep working
  with zero config; an ssh tunnel terminating locally inherits this — ssh IS
  the auth there).
- No token configured (loopback bind, no env): permissive — today's behavior.

Token sourcing: MUSE_AUTH_TOKEN env wins; else, when binding non-loopback, a
token is generated once into ~/.muse/auth_token (chmod 600).
"""

from __future__ import annotations

import secrets
from http import cookies as http_cookies
from pathlib import Path
from typing import Callable, Optional

_EXEMPT = ("/api/auth/login", "/api/auth/status")
_LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")

COOKIE_NAME = "muse_auth"


def load_or_create_token(env_token: Optional[str], state_dir: Path,
                         generate: bool) -> Optional[str]:
    """Resolve the auth token: env > ~/.muse/auth_token > (generate when asked).
    Returns None when no token exists and none should be created."""
    if env_token:
        return env_token
    token_file = state_dir / "auth_token"
    try:
        if token_file.is_file():
            tok = token_file.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        if generate:
            tok = secrets.token_urlsafe(32)
            state_dir.mkdir(parents=True, exist_ok=True)
            token_file.write_text(tok + "\n", encoding="utf-8")
            token_file.chmod(0o600)
            return tok
    except OSError:
        return None
    return None


class AuthMiddleware:
    def __init__(self, app, token_provider: Callable[[], Optional[str]],
                 allow_loopback: bool = True) -> None:
        self.app = app
        self.token_provider = token_provider
        self.allow_loopback = allow_loopback

    @staticmethod
    def _protected(path: str) -> bool:
        if path in _EXEMPT:
            return False
        return path.startswith("/api/") or path == "/mcp" or path.startswith("/mcp/")

    def _bearer_ok(self, scope, token: str) -> bool:
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                try:
                    scheme, _, cred = value.decode("latin-1").partition(" ")
                except UnicodeDecodeError:
                    return False
                return (
                    scheme.lower() == "bearer"
                    and secrets.compare_digest(cred.strip(), token)
                )
        return False

    def _cookie_ok(self, scope, token: str) -> bool:
        for name, value in scope.get("headers") or []:
            if name == b"cookie":
                jar = http_cookies.SimpleCookie()
                try:
                    jar.load(value.decode("latin-1"))
                except (http_cookies.CookieError, UnicodeDecodeError):
                    return False
                morsel = jar.get(COOKIE_NAME)
                return bool(
                    morsel and secrets.compare_digest(morsel.value, token)
                )
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("method") == "OPTIONS":  # CORS preflight must never 401
            return await self.app(scope, receive, send)
        if not self._protected(scope.get("path", "")):
            return await self.app(scope, receive, send)
        token = self.token_provider()
        if token is None:
            return await self.app(scope, receive, send)  # auth not configured
        client = scope.get("client")
        if self.allow_loopback and client and client[0] in _LOOPBACK:
            return await self.app(scope, receive, send)
        if self._bearer_ok(scope, token) or self._cookie_ok(scope, token):
            return await self.app(scope, receive, send)
        body = b'{"detail":"auth required"}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
