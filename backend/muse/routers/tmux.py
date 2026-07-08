"""Live tmux topology for the mobile panes view: detect every session/window/pane,
preview each, and respond to any of them.

Unlike the per-session cockpit (which addresses a muse session_id), this addresses
raw tmux pane ids directly — so it works for panes muse doesn't track as sessions
too. All writes still go through tmux only; nothing here touches transcript dirs.
Pane ids are validated against tmux's `%<n>` form before being used as a target.
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import options as opt
from .. import profiles as profiles_mod
from .. import slash_commands
from .. import usage_cache
from ..autopilot import sessions as live_discovery
from ..autopilot import snapshot as layout_snapshot
from ..autopilot import tmux
from ..models import PendingOption, SlashCommand, TmuxLayout, TmuxPane

router = APIRouter(prefix="/api/tmux", tags=["tmux"])

_PANE_RE = re.compile(r"^%\d+$")
_WINDOW_RE = re.compile(r"^@\d+$")
# tmux session names double as command targets, where "." and ":" are separators —
# restrict group names to a safe alphabet so they can't break `-t session:index`.
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
# Named keys safe to send into an arbitrary pane, mapped to tmux send-keys names.
_KEYS = {
    "escape": "Escape",
    "enter": "Enter",
    "tab": "Tab",
    "backspace": "BSpace",
    "delete": "DC",
    "space": "Space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
}
# Modifier prefixes on a key string: "c-c" → Ctrl-C, "m-f" → Alt-F, "c-left".
_MOD_RE = re.compile(r"^((?:[cms]-)+)(.+)$")
_FN_RE = re.compile(r"^f([1-9]|1[0-2])$")


def _resolve_key(raw: str) -> Optional[tuple[str, bool]]:
    """Resolve a UI key string to (tmux key, is_literal). is_literal means send it
    as raw text (send-keys -l), for printable characters like / | -. Returns None
    if the key isn't in the allowlist."""
    k = raw.strip()
    kl = k.lower()
    if _FN_RE.match(kl):
        return kl.upper(), False  # F1..F12
    m = _MOD_RE.match(kl)
    if m:
        prefix = m.group(1).upper()  # C- / M- / S- (combinable)
        base = m.group(2)
        if base in _KEYS:
            return prefix + _KEYS[base], False
        if len(base) == 1 and base.isprintable():
            return prefix + base, False
        return None
    if kl in _KEYS:
        return _KEYS[kl], False
    if len(k) == 1 and k.isprintable():
        return k, True  # literal char (/, |, -, …)
    return None
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# A full-width run of the same rule/border char (Claude's input box, separators)
# wraps into many junk lines on a phone — collapse long runs to a short rule.
_RULE_RE = re.compile(r"([_\-─━═—–▁])\1{11,}")


def _tail_line(cleaned: str) -> str:
    """Last visible line of a cleaned screen, ANSI-stripped and short — the
    task-list subtitle when there's no attention text."""
    for ln in reversed(cleaned.splitlines()):
        vis = _ANSI_RE.sub("", ln).strip()
        if vis:
            return vis[:100]
    return ""


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


class LaunchProfile(BaseModel):
    values: dict[str, str] = {}  # collected values for the profile's declared params
    session: str | None = None  # target group (tmux session); default = _default_session()


class GroupBody(BaseModel):
    name: str  # tmux session name — create target, or rename destination


class MoveWindow(BaseModel):
    session: str  # destination group (tmux session) for this window


class CloseWindow(BaseModel):
    cleanup: bool = False  # also run the matching profile's teardown command


def _require_pane(pane_id: str) -> str:
    if not _PANE_RE.match(pane_id):
        raise HTTPException(status_code=400, detail=f"invalid pane id: {pane_id!r}")
    return pane_id


def _require_window(window_id: str) -> str:
    if not _WINDOW_RE.match(window_id):
        raise HTTPException(status_code=400, detail=f"invalid window id: {window_id!r}")
    return window_id


def _require_session_name(name: str) -> str:
    if not _SESSION_RE.match(name):
        raise HTTPException(status_code=400, detail=f"invalid group name: {name!r}")
    return name


def _window_repr(window_id: str) -> tuple[str, str]:
    """The (cwd, window_name) of a window's representative pane (active, else first),
    looked up from the live layout. 404 if the window isn't found."""
    panes = [p for p in tmux.list_layout() if p["window_id"] == window_id]
    if not panes:
        raise HTTPException(status_code=404, detail=f"window not found: {window_id}")
    rep = next((p for p in panes if p["pane_active"]), panes[0])
    return rep["cwd"], rep["window_name"]


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
def layout(request: Request, previews: int = 1) -> TmuxLayout:
    """The whole tmux topology. `previews=0` omits the (heavy) per-pane screen
    text — the phone task list polls this shape; the deck fetches live screens
    per-pane via /panes/{id}/screen instead. Menu/mode/attention detection always
    runs server-side (it needs the capture regardless)."""
    if not tmux.available():
        return TmuxLayout(available=False, reason="tmux is not installed or not running")
    raw = tmux.list_layout()
    if not raw:
        return TmuxLayout(available=True, panes=[], reason="no tmux panes found")

    # Map panes that run a tracked Claude session → its live state (busy/idle/waiting).
    live_by_pane = {ls.pane_id: ls for ls in live_discovery.discover() if ls.pane_id}
    # Cheap (mtime-cached) per-session context-window occupancy, same source as the board.
    _, ctx_pcts = usage_cache.board_rollup()
    # Pending queued replies per session (one query) — badge "⏳n" on the task list.
    queued = request.app.state.autopilot.store.queue_counts()

    # One `tmux capture-pane` subprocess per pane: run them concurrently — a
    # ~24-pane fleet takes ~180ms serially, which is most of the poll's latency.
    with ThreadPoolExecutor(max_workers=min(8, len(raw))) as pool:
        screens = list(pool.map(lambda p: tmux.capture_visible(p["pane_id"], ansi=True), raw))

    panes: list[TmuxPane] = []
    for p, screen in zip(raw, screens):
        # The live visible screen is the only accurate, current view (scrollback is
        # a mix of stale frames from previous commands). One capture serves both the
        # colored preview and menu detection (the parser strips ANSI itself).
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
                window_id=p.get("window_id", ""),
                window_name=p["window_name"],
                window_active=p["window_active"],
                pane_index=p["pane_index"],
                pane_active=p["pane_active"],
                command=p["command"],
                cwd=p["cwd"],
                title=p["title"],
                session_attached=p["session_attached"],
                last_activity=p["last_activity"],
                muse_session_id=ls.session_id if ls else None,
                context_pct=ctx_pcts.get(ls.session_id) if ls else None,
                queued=queued.get(ls.session_id, 0) if ls else 0,
                status=status,
                attention=attention,
                mode=mode,
                preview=preview if previews else "",
                preview_tail=_tail_line(preview),
                options=options,
            )
        )
    return TmuxLayout(available=True, panes=panes)


@router.get("/panes/{pane_id}/screen")
def screen(pane_id: str) -> dict:
    """One pane's live visible screen (cleaned, ANSI kept for colors) + the menu
    and permission mode parsed from that same capture. The deck polls this for
    the pane you're actually looking at — fresher than the layout poll and a
    fraction of its payload."""
    _require_pane(pane_id)
    raw_screen = tmux.capture_visible(pane_id, ansi=True)
    preview = _clean_preview(raw_screen)
    menu = opt.parse_permission_menu(raw_screen)
    return {
        "ok": bool(raw_screen),
        "text": preview,
        "mode": opt.parse_mode(raw_screen),
        "options": [
            {"id": o.id, "label": o.label, "description": o.description, "kind": o.kind}
            for o in (menu.options if menu else [])
        ],
    }


@router.get("/panes/{pane_id}/commands", response_model=list[SlashCommand])
def commands(pane_id: str) -> list[SlashCommand]:
    """Slash commands available to this pane's Claude session — for the composer's
    "/" autocomplete. Resolves the pane's working dir from tmux, then reads the
    built-in + user + project command sets rooted there (read-only)."""
    _require_pane(pane_id)
    cwd = next((p["cwd"] for p in tmux.list_layout() if p["pane_id"] == pane_id), None)
    return [SlashCommand(**c) for c in slash_commands.list_commands(cwd)]


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
    k = body.key.strip()
    kl = k.lower()
    if kl == "accept":
        ok, err = tmux.accept_suggestion(pane_id)
    elif kl.isdigit() and len(kl) == 1 and kl != "0":
        ok, err = tmux.send_digit(pane_id, int(kl))  # menu-option select
    else:
        resolved = _resolve_key(k)
        if resolved is None:
            raise HTTPException(status_code=400, detail=f"key not allowed: {body.key!r}")
        tk, literal = resolved
        # Literal chars go as raw text (no Enter); everything else is a named/
        # modified key.
        ok, err = (
            tmux.send_text(pane_id, tk, submit=False) if literal else tmux.send_key(pane_id, tk)
        )
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


# --- Launch profiles -------------------------------------------------------------
# Named templates for opening a new window (cwd + shell command + optional params),
# hand-authored in ~/.muse/profiles.toml. muse reads them and launches; see profiles.py.


@router.get("/profiles")
def list_profiles() -> list[dict]:
    """Every launch profile (built-in default first, then the user's TOML). A broken
    config file is a 400 with the parse error so the UI can show it."""
    try:
        return [p.model_dump() for p in profiles_mod.load_profiles()]
    except profiles_mod.ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/profiles/{name}/launch")
def launch_profile(name: str, body: LaunchProfile, request: Request) -> dict:
    """Open a new window from a profile: render its cwd/command with the collected
    param values and run it via tmux, appended to the target group (or the default
    session). Returns the new pane id so the UI can jump to it."""
    if not tmux.available():
        raise HTTPException(status_code=400, detail="tmux is not running")
    try:
        profile = profiles_mod.find_profile(name)
    except profiles_mod.ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"no such profile: {name!r}")
    cwd, command = profiles_mod.render(profile, body.values)
    if not os.path.isdir(cwd):
        raise HTTPException(status_code=400, detail=f"not a directory: {cwd}")
    session = _require_session_name(body.session) if body.session else _default_session()
    label = profiles_mod.window_label(profile, body.values)
    ok, result = tmux.new_window(cwd, command, session=session, name=label or None)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {result}")
    request.app.state.autopilot.store.log(
        "tmux", "profile_launch", f"{profile.name}: {result} in {cwd}"
    )
    return {"ok": True, "pane_id": result}


# --- Session restore -------------------------------------------------------------
# muse snapshots the tmux topology (see autopilot/snapshot.py) so it can be rebuilt after
# a reboot, resuming each Claude session where it left off (`claude --resume <id>`).


class RestoreLayout(BaseModel):
    groups: list[str] = []  # which snapshot groups (tmux sessions) to rebuild


@router.get("/snapshot")
def get_snapshot(request: Request) -> dict:
    """The latest topology snapshot, annotated with per-window `live` flags and an `offer`
    flag (the snapshot has Claude windows and none are currently running — the post-reboot
    signal that drives the restore banner). Empty shape when nothing's captured yet."""
    latest = request.app.state.autopilot.store.latest_snapshot()
    if latest is None:
        return {"ts": None, "groups": [], "offer": False, "restorable_count": 0}
    return layout_snapshot.annotate_liveness(json.loads(latest[1]))


@router.post("/restore")
def restore_layout(body: RestoreLayout, request: Request) -> dict:
    """Rebuild the selected groups from the latest snapshot — recreate their tmux sessions/
    windows and resume each Claude session. Skips anything already running (no duplicates)."""
    if not tmux.available():
        raise HTTPException(status_code=400, detail="tmux is not running")
    latest = request.app.state.autopilot.store.latest_snapshot()
    if latest is None:
        raise HTTPException(status_code=400, detail="no layout snapshot to restore")
    result = layout_snapshot.restore(json.loads(latest[1]), body.groups)
    request.app.state.autopilot.store.log(
        "tmux", "layout_restore", f"{result['restored']} windows in {result['groups']}"
    )
    return {"ok": True, **result}


# --- Groups (tmux sessions) ------------------------------------------------------
# A "group" is a tmux session. Panes are organized by moving whole windows between
# sessions; muse just runs the tmux verbs and the layout poll reflects the result.


@router.post("/sessions")
def create_group(body: GroupBody, request: Request) -> dict:
    """Create a new empty group (detached tmux session). It carries one idle shell
    until windows are moved into it."""
    if not tmux.available():
        raise HTTPException(status_code=400, detail="tmux is not running")
    name = _require_session_name(body.name)
    ok, err = tmux.new_session(name)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log("tmux", "group_create", name)
    return {"ok": True, "session": name}


@router.post("/sessions/{name}/rename")
def rename_group(name: str, body: GroupBody, request: Request) -> dict:
    """Rename a group (tmux session)."""
    _require_session_name(name)
    new = _require_session_name(body.name)
    ok, err = tmux.rename_session(name, new)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log("tmux", "group_rename", f"{name} → {new}")
    return {"ok": True, "session": new}


@router.delete("/sessions/{name}")
def delete_group(name: str, request: Request) -> dict:
    """Destroy a group (tmux session) and everything running in it. The UI only
    offers this for placeholder-only groups + a confirm."""
    _require_session_name(name)
    ok, err = tmux.kill_session(name)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log("tmux", "group_delete", name)
    return {"ok": True}


@router.post("/windows/{window_id}/rename")
def rename_window(window_id: str, body: GroupBody, request: Request) -> dict:
    """Rename a window (the label shown in the list and deck tabs). Window names are
    freeform, but reject empties / control chars / absurd lengths."""
    _require_window(window_id)
    name = body.name.strip()
    if not name or len(name) > 100 or any(c in name for c in "\n\r\t"):
        raise HTTPException(status_code=400, detail=f"invalid window name: {body.name!r}")
    ok, err = tmux.rename_window(window_id, name)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log("tmux", "window_rename", f"{window_id} → {name}")
    return {"ok": True, "window_id": window_id, "name": name}


@router.post("/windows/{window_id}/move")
def move_window(window_id: str, body: MoveWindow, request: Request) -> dict:
    """Move a whole window into another group (tmux session). The window's panes keep
    running — a live Claude is undisturbed."""
    _require_window(window_id)
    dst = _require_session_name(body.session)
    ok, err = tmux.move_window(window_id, dst)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    request.app.state.autopilot.store.log("tmux", "window_move", f"{window_id} → {dst}")
    return {"ok": True, "window_id": window_id, "session": dst}


@router.get("/windows/{window_id}/cleanup")
def window_cleanup(window_id: str) -> dict:
    """Preview a window's removal: its name/cwd and, if a profile's match_cwd claims it,
    the exact cleanup command that would run (so the Remove dialog can show it)."""
    _require_window(window_id)
    cwd, name = _window_repr(window_id)
    match = profiles_mod.match_cleanup(cwd, name)
    cleanup = {"profile": match["profile"], "command": match["command"]} if match else None
    return {"window_name": name, "cwd": cwd, "cleanup": cleanup}


@router.post("/windows/{window_id}/close")
def close_window(window_id: str, body: CloseWindow, request: Request) -> dict:
    """Remove a session: kill the window, then (opt-in) run its profile's cleanup. Kill
    first so a teardown like `git worktree remove` isn't fighting a live cwd; cleanup is
    best-effort — a failure is reported, not fatal (the window is already gone)."""
    if not tmux.available():
        raise HTTPException(status_code=400, detail="tmux is not running")
    _require_window(window_id)
    cwd, name = _window_repr(window_id)
    ok, err = tmux.kill_window(window_id)
    if not ok:
        raise HTTPException(status_code=400, detail=f"tmux: {err}")
    store = request.app.state.autopilot.store
    store.log("tmux", "window_close", f"{window_id} ({name})")
    result = {"ok": True, "cleanup_ran": False, "cleanup_ok": False, "cleanup_output": ""}
    if body.cleanup:
        match = profiles_mod.match_cleanup(cwd, name)
        if match:
            c_ok, out = profiles_mod.run_cleanup(match["command"], match["run_cwd"])
            result.update(cleanup_ran=True, cleanup_ok=c_ok, cleanup_output=out)
            store.log("tmux", "window_cleanup", f"{name}: {'ok' if c_ok else 'FAILED'}")
    return result
