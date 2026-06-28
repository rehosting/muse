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
from .auth import AuthMiddleware
from .compression import GzipBufferedMiddleware
from .config import get_settings
from .alerts import AlertsWatcher
from .autopilot.controller import AutopilotController
from .mcp import build_mcp, set_service
from .board import BoardTicker
from .routers import (
    ai,
    auth,
    autopilot,
    board,
    insights,
    interact,
    investigations,
    launch,
    notify,
    options,
    sessions,
    stream,
    tmux,
    worklog,
)
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
    # Parsed usage-limit resets anchor stats' 5h window (observed > estimated).
    app.state.autopilot.on_reset = app.state.service.usage_history.record_reset
    # AI idle mode: the controller requests drafts and reads results through
    # these callables (it never imports the service or touches the AI worker).
    app.state.autopilot.enqueue_draft = app.state.service.enqueue_draft_reply
    app.state.autopilot.get_ai_job = app.state.service.ai_jobs.get
    app.state.autopilot.ai_cost_today = app.state.service.ai_jobs.cost_today
    app.state.autopilot.start()
    app.state.alerts = AlertsWatcher(app.state.service)
    app.state.alerts.start()
    # AI worker: single daemon thread executing headless `claude -p` jobs.
    get_settings().ai_workdir.mkdir(parents=True, exist_ok=True)
    app.state.service.ai_worker.start()
    # Board ticker: demand-driven; starts when the board page connects.
    app.state.board = BoardTicker(app.state.service, broker)
    lifecycle.write_pidfile(app.state.started_at)  # last startup step (we own the port now)
    # The mounted MCP sub-app's lifespan is NOT run by Starlette, so run its
    # session manager here (required even in stateless_http mode).
    async with _mcp.session_manager.run():
        try:
            yield
        finally:
            await app.state.alerts.stop()
            await app.state.board.stop()
            app.state.service.ai_worker.stop()  # kills any in-flight claude -p
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
            app.state.service.usage_history.close()
            app.state.service.packs.close()
            app.state.service.ai_jobs.close()
            app.state.service.git_index.close()
            await app.state.autopilot.stop()
            lifecycle.remove_pidfile()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="muse", version="0.1.0", lifespan=lifespan)

    # gzip for ordinary responses (big thread JSON ~11MB -> ~2.3MB over a forward).
    # Added FIRST => innermost: it compresses the final body, then auth/CORS wrap it.
    # PURE ASGI and SSE-safe by construction (passes text/event-stream through; see
    # ..compression) — do NOT swap in Starlette's GZipMiddleware, which buffers SSE.
    app.add_middleware(GzipBufferedMiddleware)

    # Token auth for non-loopback clients (no-op when no token is configured).
    # PURE ASGI — never replace with BaseHTTPMiddleware/@app.middleware("http"):
    # those buffer streaming responses and break SSE + the MCP sub-app.
    # Added BEFORE CORSMiddleware so CORS wraps it (preflights never 401).
    app.add_middleware(
        AuthMiddleware,
        token_provider=settings.resolve_auth_token,
        allow_loopback=settings.auth_allow_loopback,
    )
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
    app.include_router(launch.router)
    app.include_router(ai.router)
    app.include_router(board.router)
    app.include_router(insights.router)
    app.include_router(interact.router)
    app.include_router(options.router)
    app.include_router(tmux.router)
    app.include_router(auth.router)

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

    @app.get("/api/debug/stacks", include_in_schema=False)
    def debug_stacks() -> dict:
        """Stack of every thread — for diagnosing CPU burn without ptrace
        (py-spy needs elevated perms under yama ptrace_scope=1)."""
        import sys
        import threading as _threading
        import traceback

        names = {t.ident: t.name for t in _threading.enumerate()}
        out = {}
        for tid, frame in sys._current_frames().items():
            out[f"{names.get(tid, '?')}-{tid}"] = traceback.format_stack(frame)
        return out

    @app.get("/api/debug/tasks", include_in_schema=False)
    async def debug_tasks() -> dict:
        """Every live asyncio task + its current stack — the thread-stack dump
        can't see a spinning coroutine (the event loop thread just shows
        `uvicorn.run`), so this is what pinpoints an event-loop spin."""
        import asyncio

        out = {}
        for i, task in enumerate(asyncio.all_tasks()):
            frames = task.get_stack()
            out[f"{task.get_name()}-{i}"] = {
                "coro": str(task.get_coro()),
                "done": task.done(),
                "stack": [
                    f"{fr.f_code.co_filename.split('/muse/')[-1]}:{fr.f_lineno} {fr.f_code.co_name}"
                    for fr in frames
                ],
            }
        return out

    @app.get("/api/debug/loop", include_in_schema=False)
    async def debug_loop() -> dict:
        """Event-loop internals — call this WHILE CPU is pegged to find a spin the
        task/thread dumps can't see. `ready` constantly non-empty => a callback
        re-scheduling itself; a selector fd count far above the live connection
        count => a half-closed fd the loop keeps waking on (the suspected cause of
        the 100%-CPU spin under proxy use)."""
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            fdmap = loop._selector.get_map() or {}  # type: ignore[attr-defined]
        except Exception:
            fdmap = {}
        ready = list(getattr(loop, "_ready", []))
        # The reprs of what's perpetually in the ready queue ARE the spin: a handle
        # that reappears every sample is a callback re-scheduling itself.
        ready_reprs: list[str] = []
        for h in ready[:30]:
            try:
                cb = getattr(h, "_callback", None)
                args = getattr(h, "_args", None)
                detail = repr(cb)
                if args:
                    detail += " | " + " ".join(repr(a)[:80] for a in args)
                ready_reprs.append(detail[:200])
            except Exception:
                ready_reprs.append(repr(h)[:200])
        # Per-fd selector registration (a half-dead fd kept readable = an fd spin).
        fds = []
        for key in list(fdmap.values())[:30]:
            try:
                fds.append({"fd": key.fd, "events": key.events, "data": repr(key.data)[:120]})
            except Exception:
                pass
        return {
            "ready": len(ready),
            "scheduled": len(getattr(loop, "_scheduled", [])),
            "selector_fds": len(fdmap),
            "tasks": len(asyncio.all_tasks()),
            "ready_callbacks": ready_reprs,
            "fds": fds,
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
            # Serve real files that exist at the dist root (manifest, icons,
            # favicon, robots) before falling back to the SPA shell.
            if full_path:
                candidate = (_FRONTEND_DIST / full_path).resolve()
                if (
                    _FRONTEND_DIST.resolve() in candidate.parents
                    and candidate.is_file()
                ):
                    return FileResponse(candidate)
            # index.html is never cached: a rebuild changes the hashed asset
            # names it references, and we ship no service worker — a stale shell
            # would point at deleted bundles.
            return FileResponse(
                _FRONTEND_DIST / "index.html",
                headers={"Cache-Control": "no-cache"},
            )

    return app


app = create_app()
