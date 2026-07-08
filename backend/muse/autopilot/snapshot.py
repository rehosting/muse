"""Snapshot the tmux topology so it can be rebuilt after a reboot ("session restore").

tmux (sessions/windows/panes) is wiped on reboot, but Claude Code conversations persist on
disk (~/.claude/projects/<encoded-cwd>/<session-id>.jsonl) and resume with
`claude --resume <id>`. We periodically capture the structure — which Claude sessions exist,
how they're grouped, their cwds/names — and on demand recreate the windows, resuming each
Claude where it left off. Window granularity only (v1 doesn't restore intra-window splits).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from datetime import datetime, timezone

from . import sessions as live_discovery
from . import tmux


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rep_pane(panes: list[dict], pane_sid: dict[str, str]) -> dict:
    """The pane that best represents a window: the Claude pane (one with a resolved
    session id) if any, else the active pane, else the lowest-index pane."""
    return (
        next((p for p in panes if pane_sid.get(p["pane_id"])), None)
        or next((p for p in panes if p["pane_active"]), None)
        or min(panes, key=lambda p: p["pane_index"])
    )


def build_snapshot() -> dict | None:
    """Capture the current topology, or None when there's nothing worth keeping (tmux
    down, or no Claude windows) — so we never clobber a good last-known-good with an
    empty capture around a reboot."""
    if not tmux.available():
        return None
    panes = tmux.list_layout()
    if not panes:
        return None
    pane_sid = {s.pane_id: s.session_id for s in live_discovery.discover() if s.pane_id}

    # session_name -> window_index -> [panes], preserving tmux's natural order.
    grouped: dict[str, dict[int, list[dict]]] = {}
    for p in panes:
        grouped.setdefault(p["session_name"], {}).setdefault(p["window_index"], []).append(p)

    out_groups: list[dict] = []
    claude_windows = 0
    for sname, windows in grouped.items():
        wins: list[dict] = []
        for widx in sorted(windows):
            rep = _rep_pane(windows[widx], pane_sid)
            sid = pane_sid.get(rep["pane_id"])
            is_claude = sid is not None or rep["command"] == "claude"
            if is_claude:
                claude_windows += 1
            wins.append(
                {
                    "window_name": rep["window_name"],
                    "cwd": rep["cwd"],
                    "command": rep["command"],
                    "kind": "claude" if is_claude else "shell",
                    "session_id": sid,
                }
            )
        out_groups.append({"name": sname, "windows": wins})

    if claude_windows == 0:
        return None
    return {"ts": _now(), "groups": out_groups}


def topology_sig(snapshot: dict) -> str:
    """Stable hash of the structure (NOT the timestamp) for cheap change detection."""
    parts = [
        f"{g['name']}|{w['window_name']}|{w['cwd']}|{w.get('session_id') or ''}|{w['kind']}"
        for g in snapshot["groups"]
        for w in g["windows"]
    ]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def _live_index() -> tuple[set[str], set[tuple[str, str, str]], set[str]]:
    """(live Claude session ids, live (session,window,cwd) triples, live session names)
    from the current tmux + Claude discovery — used to skip windows already running."""
    live_sids = {s.session_id for s in live_discovery.discover() if s.session_id}
    layout = tmux.list_layout()
    live_wins = {(p["session_name"], p["window_name"], p["cwd"]) for p in layout}
    session_names = {p["session_name"] for p in layout}
    return live_sids, live_wins, session_names


def _is_live(gname: str, w: dict, live_sids: set[str], live_wins: set[tuple[str, str, str]]) -> bool:
    """Whether a snapshot window is already running. Claude windows key on their session
    id (strong); shell windows fall back to (group, name, cwd)."""
    sid = w.get("session_id")
    if sid:
        return sid in live_sids
    return (gname, w["window_name"], w["cwd"]) in live_wins


def annotate_liveness(snapshot: dict) -> dict:
    """Return the snapshot with a per-window `live` flag plus `offer`/`restorable_count`.
    `offer` is the clean-reboot signal that drives the UI banner: the snapshot has ≥2
    Claude windows and none of them are currently running."""
    live_sids, live_wins, _ = _live_index()
    groups_out: list[dict] = []
    claude_total = claude_live = restorable = 0
    for g in snapshot["groups"]:
        wins = []
        for w in g["windows"]:
            live = _is_live(g["name"], w, live_sids, live_wins)
            if w["kind"] == "claude":
                claude_total += 1
                claude_live += 1 if live else 0
            if not live:
                restorable += 1
            wins.append({**w, "live": live})
        groups_out.append({"name": g["name"], "windows": wins})
    return {
        "ts": snapshot["ts"],
        "groups": groups_out,
        "offer": claude_total >= 2 and claude_live == 0,
        "restorable_count": restorable,
    }


def _command_for(w: dict) -> str:
    """The shell command to launch a restored window. Claude windows resume the exact
    session (falling back to the latest conversation in the cwd if that fails); a Claude
    window whose id we couldn't capture continues the latest; shells open bare."""
    if w["kind"] != "claude":
        return ""  # empty → tmux opens the default shell
    sid = w.get("session_id")
    if sid:
        return f"claude --resume {shlex.quote(sid)} || claude --continue"
    return "claude --continue"


def restore(snapshot: dict, group_names: list[str], only_missing: bool = True) -> dict:
    """Rebuild the selected groups from a snapshot. Creates each group's tmux session if
    absent and (re)creates its windows in order, skipping any already running so partial
    or repeated restores never duplicate. Returns {restored, skipped, groups}."""
    live_sids, live_wins, session_names = _live_index()
    wanted = set(group_names)
    restored = skipped = 0
    touched: list[str] = []

    for g in snapshot["groups"]:
        if g["name"] not in wanted:
            continue
        to_make = [
            w
            for w in g["windows"]
            if not (only_missing and _is_live(g["name"], w, live_sids, live_wins))
        ]
        skipped += len(g["windows"]) - len(to_make)
        if not to_make:
            continue
        touched.append(g["name"])
        session_exists = g["name"] in session_names
        for w in to_make:
            cwd = w["cwd"] if w["cwd"] and os.path.isdir(w["cwd"]) else os.path.expanduser("~")
            cmd = _command_for(w)
            if not session_exists:
                ok, _ = tmux.new_session(
                    g["name"], cwd=cwd, command=cmd or None, window_name=w["window_name"]
                )
                session_exists = True
            else:
                ok, _ = tmux.new_window(cwd, cmd, session=g["name"], name=w["window_name"])
            restored += 1 if ok else 0
    return {"restored": restored, "skipped": skipped, "groups": touched}
