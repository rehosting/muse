"""Live tmux topology for the mobile panes view: detect every session/window/pane,
preview each, and respond to any of them.

Unlike the per-session cockpit (which addresses a muse session_id), this addresses
raw tmux pane ids directly — so it works for panes muse doesn't track as sessions
too. All writes still go through tmux only; nothing here touches transcript dirs.
Pane ids are validated against tmux's `%<n>` form before being used as a target.
"""

from __future__ import annotations

import os
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import options as opt
from .. import usage_cache
from ..autopilot import sessions as live_discovery
from ..autopilot import tmux
from ..models import PendingOption, TmuxLayout, TmuxPane

router = APIRouter(prefix="/api/tmux", tags=["tmux"])

_PANE_RE = re.compile(r"^%\d+$")
# Named keys safe to send into an arbitrary pane for menu navigation.
_KEYS = {"escape": "Escape", "enter": "Enter", "up": "Up", "down": "Down"}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# A full-width run of the same rule/border char (Claude's input box, separators)
# wraps into many junk lines on a phone — collapse long runs to a short rule.
_RULE_RE = re.compile(r"([_\-─━═—–▁])\1{11,}")


def _clean_preview(text: str) -> str:
    """Tidy a captured pane for display: right-trim each line, drop blank
    leading/trailing lines, and collapse runs of blank lines to one. ANSI-aware:
    a line is 'blank' only if it has no VISIBLE characters (color codes alone don't
    count), but the codes are kept so the UI can render colors."""
    out: list[str] = []
    raw = [ln.rstrip() for ln in text.splitlines()]
    visible = [_ANSI_RE.sub("", ln).strip() for ln in raw]
    start, end = 0, len(raw)
    while start < end and not visible[start]:
        start += 1
    while end > start and not visible[end - 1]:
        end -= 1
    for i in range(start, end):
        if not visible[i] and out and not _ANSI_RE.sub("", out[-1]).strip():
            continue  # collapse 2+ consecutive blanks
        out.append(_RULE_RE.sub(lambda m: m.group(1) * 6, raw[i]))
    return "\n".join(out)


class PaneSend(BaseModel):
    text: str
    submit: bool = True


class PaneKey(BaseModel):
    key: str  # escape | enter | up | down | accept | a digit "1".."9"


class NewSession(BaseModel):
    cwd: str | None = None  # where to start Claude (default: home)


def _require_pane(pane_id: str) -> str:
    if not _PANE_RE.match(pane_id):
        raise HTTPException(status_code=400, detail=f"invalid pane id: {pane_id!r}")
    return pane_id


def _default_session() -> str | None:
    """Pick the tmux session to add a new window to: the attached one with the most
    windows (your main workspace), falling back to whatever has the most windows."""
    panes = tmux.list_layout()
    if not panes:
        return None
    windows: dict[str, set] = {}
    attached: set = set()
    for p in panes:
        windows.setdefault(p["session_name"], set()).add(p["window_index"])
        if p["session_attached"]:
            attached.add(p["session_name"])
    pool = attached or set(windows)
    return max(pool, key=lambda s: len(windows.get(s, ())), default=None)


@router.get("/layout", response_model=TmuxLayout)
def layout(request: Request) -> TmuxLayout:
    if not tmux.available():
        return TmuxLayout(available=False, reason="tmux is not installed or not running")
    raw = tmux.list_layout()
    if not raw:
        return TmuxLayout(available=True, panes=[], reason="no tmux panes found")

    # Map panes that run a tracked Claude session → its live state (busy/idle/waiting).
    live_by_pane = {ls.pane_id: ls for ls in live_discovery.discover() if ls.pane_id}
    # Cheap (mtime-cached) per-session context-window occupancy, same source as the board.
    _, ctx_pcts = usage_cache.board_rollup()

    panes: list[TmuxPane] = []
    for p in raw:
        # The live visible screen is the only accurate, current view (scrollback is
        # a mix of stale frames from previous commands). One capture serves both the
        # colored preview and menu detection (the parser strips ANSI itself).
        screen = tmux.capture_visible(p["pane_id"], ansi=True)
        preview = _clean_preview(screen)
        menu = opt.parse_permission_menu(screen)
        options = (
            [
                PendingOption(id=o.id, label=o.label, description=o.description, kind=o.kind)
                for o in menu.options
            ]
            if menu
            else []
        )
        ls = live_by_pane.get(p["pane_id"])
        status, attention = _attention(ls, bool(options))
        # Every Claude pane has a mode; its "default" footer drops the status-line
        # marker, so fall back to default rather than hiding the (still tappable) chip.
        mode = opt.parse_mode(screen)
        if mode is None and p["command"] == "claude":
            mode = "default"
        panes.append(
            TmuxPane(
                pane_id=p["pane_id"],
                session_name=p["session_name"],
                window_index=p["window_index"],
                window_name=p["window_name"],
                window_active=p["window_active"],
                pane_index=p["pane_index"],
                pane_active=p["pane_active"],
                command=p["command"],
                cwd=p["cwd"],
                title=p["title"],
                session_attached=p["session_attached"],
                muse_session_id=ls.session_id if ls else None,
                context_pct=ctx_pcts.get(ls.session_id) if ls else None,
                status=status,
                attention=attention,
                mode=mode,
                preview=preview,
                options=options,
            )
        )
    return TmuxLayout(available=True, panes=panes)


def _attention(ls, has_menu: bool) -> tuple[str, str]:
    """Classify a pane for the mobile task list: what does it need from the user?

    A visible ❯ menu or a session blocked on input → needs_you; an actively running
    agent → working; everything else (plain shells, idle agents) → idle."""
    if has_menu:
        return "needs_you", "choose an option"
    if ls is not None:
        if ls.waiting_for or ls.status == "waiting":
            return "needs_you", f"waiting for {ls.waiting_for or 'you'}"
        if ls.status == "busy":
            return "working", "Claude is working"
        if ls.status == "idle":
            return "responded", "response ready — your reply"
    return "idle", ""


@router.get("/panes/{pane_id}/capture")
def capture(pane_id: str, lines: int = 40) -> dict:
    _require_pane(pane_id)
    text = tmux.capture_pane(pane_id, max(5, min(lines, 200)))
    return {"ok": bool(text), "text": text}


@router.post("/panes/{pane_id}/send")
def send(pane_id: str, body: PaneSend, request: Request) -> dict:
    _require_pane(pane_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    ok, err = tmux.send_text(pane_id, text, submit=body.submit)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log("tmux", "pane_send", f"{pane_id} ← {text[:80]}")
    return {"ok": True, "pane_id": pane_id}


@router.post("/panes/{pane_id}/key")
def key(pane_id: str, body: PaneKey, request: Request) -> dict:
    _require_pane(pane_id)
    k = body.key.strip().lower()
    if k == "accept":
        ok, err = tmux.accept_suggestion(pane_id)
    elif k.isdigit() and len(k) == 1 and k != "0":
        ok, err = tmux.send_digit(pane_id, int(k))
    elif k in _KEYS:
        ok, err = tmux.send_key(pane_id, _KEYS[k])
    else:
        raise HTTPException(status_code=400, detail=f"key not allowed: {body.key!r}")
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log("tmux", "pane_key", f"{pane_id} ← {k}")
    return {"ok": True, "pane_id": pane_id}


@router.post("/panes/{pane_id}/mode")
def cycle_mode(pane_id: str, request: Request) -> dict:
    """Cycle Claude Code's permission mode in a pane (one Shift+Tab) and report where
    it landed. Re-reads the status line after a short repaint delay so the UI can
    confirm the new mode rather than guess the cycle order."""
    _require_pane(pane_id)
    ok, err = tmux.cycle_mode(pane_id)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    time.sleep(0.25)  # let Claude repaint the status line
    mode = opt.parse_mode(tmux.capture_visible(pane_id, ansi=True))
    request.app.state.autopilot.store.log("tmux", "pane_mode", f"{pane_id} → {mode}")
    return {"ok": True, "pane_id": pane_id, "mode": mode}


@router.post("/new")
def new_session(body: NewSession, request: Request) -> dict:
    """Open a new tmux window running Claude and return its pane id so the UI can
    jump straight to it. Defaults the working dir to home and the session to your
    main (attached, most-windows) workspace."""
    if not tmux.available():
        raise HTTPException(status_code=400, detail="tmux is not running")
    cwd = body.cwd or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        raise HTTPException(status_code=400, detail=f"not a directory: {cwd}")
    ok, result = tmux.new_window(cwd, "claude", session=_default_session())
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {result}")
    request.app.state.autopilot.store.log("tmux", "new_session", f"{result} in {cwd}")
    return {"ok": True, "pane_id": result}
