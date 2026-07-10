"""Surface the choices a live session is presenting, and act on a selection.

This is the sanctioned, *observable* counterpart to interact.py's deliberately tiny
key whitelist: the buffer/transcript is parsed into a concrete option list, and a
selection is only sent after re-deriving the options at send time and confirming the
client's fingerprint still matches — so we can never act on a menu that changed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import options as opt
from ..autopilot import sessions as live_discovery
from ..autopilot import tmux
from ..models import PendingOption, PendingOptions

router = APIRouter(prefix="/api", tags=["options"])


class SelectRequest(BaseModel):
    option_id: str
    fingerprint: str
    method: str = "digit"  # digit | arrow
    free_text: str | None = None


def _to_api(menu: opt.ParsedMenu, session_id: str, pane_id: str) -> PendingOptions:
    options = [
        PendingOption(id=o.id, label=o.label, description=o.description, kind=o.kind)
        for o in menu.options
    ]
    return PendingOptions(
        session_id=session_id,
        source=menu.source,
        available=True,
        prompt=menu.prompt,
        detail=menu.detail,
        options=options,
        current_index=menu.current_index,
        fingerprint=opt.fingerprint(menu.prompt, menu.options, menu.detail),
        remaining_questions=menu.remaining_questions,
        pane_id=pane_id,
    )


def _resolve(session_id: str, request: Request) -> tuple[PendingOptions, opt.ParsedMenu | None]:
    """Current pending options for a session. Returns (api_model, parsed_menu|None)."""
    ls = next((s for s in live_discovery.discover() if s.session_id == session_id), None)
    if ls is None:
        return (
            PendingOptions(session_id=session_id, reason="session has no live process"),
            None,
        )
    if not ls.pane_id:
        return (
            PendingOptions(
                session_id=session_id,
                in_tmux=False,
                pane_id=None,
                reason="process found but not running inside tmux",
            ),
            None,
        )
    pane = ls.pane_id

    # 1) Permission/selection dialog visible in the live pane (the fragile source).
    text = tmux.capture_pane(pane, 40)
    menu = opt.parse_permission_menu(text)
    if menu is not None:
        return _to_api(menu, session_id, pane), menu

    # 2) Structured tool question pending in the transcript (exact).
    thread = request.app.state.service.get_thread(session_id)
    menu = opt.find_pending_tool_question(thread)
    if menu is not None:
        return _to_api(menu, session_id, pane), menu

    return (
        PendingOptions(session_id=session_id, pane_id=pane, reason="nothing pending"),
        None,
    )


@router.get("/sessions/{session_id}/options")
def get_options(session_id: str, request: Request) -> PendingOptions:
    api, _ = _resolve(session_id, request)
    return api


@router.post("/sessions/{session_id}/options/select")
def select_option(
    session_id: str, body: SelectRequest, request: Request, response: Response
) -> dict:
    api, menu = _resolve(session_id, request)
    if not api.available or menu is None:
        # Free-text is still actionable even if no menu is parsed (e.g. plain prompt).
        if body.free_text:
            return _send_free_text(session_id, body.free_text, request)
        raise HTTPException(status_code=400, detail=api.reason or "no options pending")

    pane = api.pane_id
    chosen = next((o for o in menu.options if o.id == body.option_id), None)
    if chosen is None:
        raise HTTPException(status_code=400, detail=f"unknown option: {body.option_id!r}")

    # Free-text path bypasses the menu fingerprint check (it's not a menu pick).
    if chosen.kind == "free_text" or body.free_text:
        if not body.free_text:
            raise HTTPException(status_code=400, detail="free_text required for this option")
        return _send_free_text(session_id, body.free_text, request)

    # Stale-menu guard: refuse if what's pending now differs from what the user saw.
    if api.fingerprint != body.fingerprint:
        response.status_code = 409
        return {"ok": False, "error": "menu changed; re-fetch options", "options": api.model_dump()}

    target_index = menu.options.index(chosen)
    if body.method == "arrow":
        current = menu.current_index if menu.current_index is not None else 0
        ok, err = tmux.select_in_menu(pane, target_index, current)
    else:
        ok, err = tmux.send_digit(pane, int(chosen.id))
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")

    request.app.state.autopilot.store.log(
        session_id, "user_select", f"{pane} ← opt {chosen.id} ({chosen.label[:40]})"
    )
    return {"ok": True, "pane_id": pane, "method": body.method, "sent_index": target_index}


def _send_free_text(session_id: str, text: str, request: Request) -> dict:
    ls = next((s for s in live_discovery.discover() if s.session_id == session_id), None)
    if ls is None or not ls.pane_id:
        raise HTTPException(status_code=400, detail="session has no live tmux pane")
    ok, err = tmux.send_text(ls.pane_id, text.strip(), submit=True)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log(
        session_id, "user_select", f"{ls.pane_id} ← free_text {text[:60]}"
    )
    return {"ok": True, "pane_id": ls.pane_id, "method": "free_text"}
