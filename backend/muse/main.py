"""FastAPI application factory.

The app is a genuine long-running service: its lifespan owns the event broker,
the session service, and the tailer registry. The future job/worker + tmux layer
hooks into this same lifespan.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db, lifecycle
from .config import get_settings
from .alerts import AlertsWatcher
from .autopilot.controller import AutopilotController
from .mcp import build_mcp, set_service
from .routers import autopilot, investigations, notify, sessions, stream, worklog
from .services.events import EventBroker
from .services.session_service import SessionService

# The MCP server (mounted at /mcp). Tools resolve the shared SessionService via
# set_service() at startup; its session manager runs inside the app lifespan.
_mcp = build_mcp()

# Built frontend (vite build -> frontend/dist). Optional; dev uses the vite proxy.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start alongside a live instance (prevents stale processes piling up
    # and silently serving old code). Raises SystemExit if one is already running.
    lifecycle.ensure_single_instance()
    app.state.started_at = time.time()
    broker = EventBroker()
    app.state.broker = broker
    app.state.service = SessionService(broker)
    set_service(app.state.service)  # expose the shared service to MCP tools
    # Drain any WAL inherited from a crashed/killed predecessor so we don't grow it.
    db.checkpoint(app.state.service.store._conn)
    app.state.autopilot = AutopilotController()
    app.state.autopilot.start()
    app.state.alerts = AlertsWatcher(app.state.service)
    app.state.alerts.start()
    lifecycle.write_pidfile(app.state.started_at)  # last startup step (we own the port now)
    # The mounted MCP sub-app's lifespan is NOT run by Starlette, so run its
    # session manager here (required even in stateless_http mode).
    async with _mcp.session_manager.run():
        try:
            yield
        finally:
            await app.state.alerts.stop()
            await app.state.service.tailers.stop_all()
            # Checkpoint+truncate the WAL on a still-open connection before closing.
            db.checkpoint(app.state.service.store._conn)
            app.state.service.store.close()
            app.state.service.search_index.close()
            app.state.service.notify_store.close()
            app.state.service.investigations.close()
            app.state.service.worklog.close()
            app.state.service.file_index.close()
            app.state.service.health.close()
            await app.state.autopilot.stop()
            lifecycle.remove_pidfile()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="muse", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(sessions.router)
    app.include_router(stream.router)
    app.include_router(autopilot.router)
    app.include_router(notify.router)
    app.include_router(investigations.router)
    app.include_router(worklog.router)

    # MCP server (Streamable HTTP) on the same process → tool calls share state
    # with the web UI. The sub-app serves at /mcp/; redirect the canonical bare
    # /mcp (what `claude mcp add … http://127.0.0.1:8848/mcp` configures) to it,
    # since the SPA catch-all would otherwise 405 the bare path. 307 preserves the
    # POST method + body. Registered before the SPA catch-all so /mcp wins.
    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def _mcp_redirect() -> RedirectResponse:
        return RedirectResponse("/mcp/", status_code=307)

    app.mount("/mcp", _mcp.streamable_http_app())

    @app.get("/api/health")
    def health() -> dict:
        started_at = getattr(app.state, "started_at", None)
        return {
            "status": "ok",
            "claude_dir": str(settings.claude_dir),
            "projects_dir_exists": settings.projects_dir.is_dir(),
            "watching": app.state.service.tailers.watching(),
            **lifecycle.version_info(started_at),
        }

    @app.get("/api/version")
    def version() -> dict:
        """Running code's version + git sha + uptime — compare git_sha to the
        checked-out repo to tell whether the live process is current or stale."""
        return lifecycle.version_info(getattr(app.state, "started_at", None))

    # Serve the built SPA if present (production); harmless when absent (dev).
    if _FRONTEND_DIST.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=_FRONTEND_DIST / "assets"),
            name="assets",
        )

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            index = _FRONTEND_DIST / "index.html"
            return FileResponse(index)

    return app


app = create_app()
