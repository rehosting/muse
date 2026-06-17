"""Login/logout/status for remote access. Login exchanges the token for an
HttpOnly cookie (EventSource can't send Authorization headers; same-origin
cookies ride along automatically). SameSite=Lax so a ntfy-notification tap —
a top-level navigation — arrives authenticated."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import COOKIE_NAME
from ..config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_YEAR = 365 * 24 * 3600


class LoginRequest(BaseModel):
    token: str


def _scheme(request: Request) -> str:
    return request.headers.get("x-forwarded-proto") or request.url.scheme


@router.post("/login", status_code=204)
def login(body: LoginRequest, request: Request, response: Response) -> Response:
    token = get_settings().resolve_auth_token()
    if token is None:
        raise HTTPException(status_code=503, detail="no auth token configured")
    if not secrets.compare_digest(body.token.strip(), token):
        raise HTTPException(status_code=403, detail="wrong token")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_YEAR,
        httponly=True,
        samesite="lax",
        # Secure only over https: tailscale-serve terminates TLS; a raw
        # tailnet http URL must still be able to set the cookie.
        secure=_scheme(request) == "https",
        path="/",
    )
    response.status_code = 204
    return response


@router.post("/logout", status_code=204)
def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/status")
def status(request: Request) -> dict:
    token = get_settings().resolve_auth_token()
    if token is None:
        return {"auth_required": False, "authenticated": True}
    client_ok = bool(
        request.client
        and get_settings().auth_allow_loopback
        and request.client.host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
    )
    cookie_ok = secrets.compare_digest(
        request.cookies.get(COOKIE_NAME, ""), token
    )
    auth_header = request.headers.get("authorization", "")
    bearer_ok = auth_header.lower().startswith("bearer ") and secrets.compare_digest(
        auth_header[7:].strip(), token
    )
    return {
        "auth_required": True,
        "authenticated": client_ok or cookie_ok or bearer_ok,
    }
