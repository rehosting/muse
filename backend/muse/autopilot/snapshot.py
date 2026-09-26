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


CODEX_PREFIX = "codex:"
# Window kinds that carry a resumable agent conversation (vs. a plain shell).
AGENT_KINDS = ("claude", "codex")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kind_for(sid: str | None, command: str) -> str:
    """Classify a window from its resolved session id (strong) or foreground command."""
    if (sid or "").startswith(CODEX_PREFIX) or command == "codex":
        return "codex"
    if sid or command == "claude":
        return "claude"
    return "shell"


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
    agent_windows = 0
    for sname, windows in grouped.items():
        wins: list[dict] = []
        for widx in sorted(windows):
            rep = _rep_pane(windows[widx], pane_sid)
            sid = pane_sid.get(rep["pane_id"])
            kind = _kind_for(sid, rep["command"])
            if kind in AGENT_KINDS:
                agent_windows += 1
            wins.append(
                {
                    "window_name": rep["window_name"],
                    "cwd": rep["cwd"],
                    "command": rep["command"],
                    "kind": kind,
                    "session_id": sid,
                }
            )
        out_groups.append({"name": sname, "windows": wins})

    if agent_windows == 0:
        return None
    return {"ts": _now(), "groups": out_groups}


def agent_window_count(snapshot: dict) -> int:
    """How many resumable agent windows a snapshot holds — its "richness"."""
    return sum(
        1 for g in snapshot.get("groups", []) for w in g.get("windows", []) if _is_agent(w)
    )


def _is_agent(w: dict) -> bool:
    """Agent window in either a fresh capture (kind) or a legacy one (codex: id)."""
    return w.get("kind") in AGENT_KINDS or (w.get("session_id") or "").startswith(CODEX_PREFIX)


def pick_restorable(rows: list[tuple[str, str]]) -> dict | None:
    """Choose the last-known-good snapshot from the newest-first history ring: the row
    holding the most agent windows, newest on a tie. The newest row alone is the wrong
    answer right after a reboot — the capture that lands a minute in sees only the single
    window you opened by hand, while the pre-reboot topology sits one row back."""
    best: dict | None = None
    best_count = -1
    for _sig, raw in rows:
        try:
            snap = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(snap, dict) or not snap.get("groups"):
            continue
        count = agent_window_count(snap)
        if count > best_count:  # strict: ties keep the newer row (rows are newest-first)
            best, best_count = snap, count
    return best


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
    agent windows and none of them are currently running."""
    live_sids, live_wins, _ = _live_index()
    groups_out: list[dict] = []
    claude_total = claude_live = restorable = 0
    for g in snapshot["groups"]:
        wins = []
        for w in g["windows"]:
            live = _is_live(g["name"], w, live_sids, live_wins)
            if _is_agent(w):
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
    """The shell command to launch a restored window, resuming the exact conversation with
    the CLI that owns it (falling back to that CLI's latest conversation if the id is
    gone). Codex threads carry a `codex:` id prefix and must be resumed with `codex
    resume` — feeding one to `claude --resume` just errors out. Shells open bare."""
    sid = w.get("session_id") or ""
    if sid.startswith(CODEX_PREFIX) or w.get("kind") == "codex":
        thread = sid[len(CODEX_PREFIX) :]
        if thread:
            return f"codex resume {shlex.quote(thread)} || codex resume --last"
        return "codex resume --last"
    if w.get("kind") != "claude":
        return ""  # empty → tmux opens the default shell
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
