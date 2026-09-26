"""Discover live provider sessions and match them to tmux panes.

Claude exposes pid->session sidecars under ~/.claude/sessions/*.json, which is
the primary source of truth for live Claude panes. Codex does not write an
equivalent sidecar; instead, its live process logs thread_ids into
~/.codex/logs_*.sqlite and stores thread metadata in ~/.codex/state_*.sqlite.
We resolve a tmux pane to its descendant Codex process, then map that pid to
the active thread id through those SQLite logs.
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ..config import get_settings
from ..models import LiveSession
from . import tmux


def _alive(pid: int) -> bool:
    return os.path.exists(f"/proc/{pid}")


def _ppid(pid: int) -> Optional[int]:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            data = f.read()
        after = data[data.rfind(")") + 2 :].split()
        return int(after[1])
    except (OSError, IndexError, ValueError):
        return None


def _pane_for(pid: int, pane_pids: dict[int, str]) -> Optional[str]:
    p: Optional[int] = pid
    for _ in range(12):
        if p in pane_pids:
            return pane_pids[p]
        p = _ppid(p) if p else None
        if not p or p <= 1:
            break
    return None


def _proc_snapshot() -> dict[int, dict[str, object]]:
    out: dict[int, dict[str, object]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                data = f.read()
            start = data.find("(")
            end = data.rfind(")")
            comm = data[start + 1 : end]
            after = data[end + 2 :].split()
            ppid = int(after[1])
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        except (OSError, ValueError, IndexError):
            continue
        out[pid] = {"ppid": ppid, "comm": comm, "cmdline": raw}
    return out


def _children(procs: dict[int, dict[str, object]]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for pid, info in procs.items():
        ppid = int(info["ppid"])
        out.setdefault(ppid, []).append(pid)
    return out


def _is_codex_proc(info: dict[str, object]) -> bool:
    comm = os.path.basename(str(info.get("comm") or "")).lower()
    if comm == "codex":
        return True
    cmd = str(info.get("cmdline") or "").lower()
    argv0 = os.path.basename(cmd.split(" ", 1)[0]) if cmd else ""
    return argv0 == "codex"


def _find_descendant(
    root_pid: int,
    procs: dict[int, dict[str, object]],
    kids: dict[int, list[int]],
    pred,
) -> Optional[int]:
    if root_pid in procs and pred(procs[root_pid]):
        return root_pid
    queue = list(kids.get(root_pid, ()))
    seen = set(queue)
    while queue:
        pid = queue.pop(0)
        info = procs.get(pid)
        if info and pred(info):
            return pid
        for child in kids.get(pid, ()):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return None


def _codex_dbs() -> tuple[Optional[str], Optional[str]]:
    root = get_settings().codex_dir
    state = sorted(root.glob("state_*.sqlite"))
    logs = sorted(root.glob("logs_*.sqlite"))
    return (str(state[-1]) if state else None, str(logs[-1]) if logs else None)


def _codex_meta_for_pid(pid: int, state_db: str, logs_db: str) -> Optional[dict[str, object]]:
    try:
        lconn = sqlite3.connect(f"file:{logs_db}?mode=ro", uri=True)
        try:
            row = lconn.execute(
                "SELECT thread_id FROM logs "
                "WHERE process_uuid LIKE ? AND thread_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (f"pid:{pid}:%",),
            ).fetchone()
        finally:
            lconn.close()
    except sqlite3.Error:
        return None
    if row is None or not row[0]:
        return None
    thread_id = str(row[0])
    try:
        sconn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
        try:
            srow = sconn.execute(
                "SELECT id, cwd, cli_version, updated_at_ms FROM threads WHERE id=?",
                (thread_id,),
            ).fetchone()
        finally:
            sconn.close()
    except sqlite3.Error:
        return None
    if srow is None:
        return {"thread_id": thread_id}
    return {
        "thread_id": str(srow[0]),
        "cwd": srow[1],
        "version": srow[2],
        "updated_at_ms": srow[3],
    }


def _discover_claude(pane_pids: dict[int, str]) -> list[LiveSession]:
    sessions_dir = get_settings().claude_dir / "sessions"
    if not sessions_dir.is_dir():
        return []

    # Latest record per sessionId among live pids.
    best: dict[str, dict] = {}
    for path in glob.glob(str(sessions_dir / "*.json")):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = d.get("pid")
        sid = d.get("sessionId")
        if not pid or not sid or not _alive(pid):
            continue
        prev = best.get(sid)
        if prev is None or d.get("updatedAt", 0) > prev.get("updatedAt", 0):
            best[sid] = d

    out: list[LiveSession] = []
    for sid, d in best.items():
        pid = d["pid"]
        updated = d.get("updatedAt")
        out.append(
            LiveSession(
                session_id=sid,
                pid=pid,
                cwd=d.get("cwd"),
                status=d.get("status", "unknown"),
                waiting_for=d.get("waitingFor"),
                pane_id=_pane_for(pid, pane_pids),
                version=d.get("version"),
                updated_at=(
                    datetime.fromtimestamp(updated / 1000, tz=timezone.utc) if updated else None
                ),
            )
        )
    return out


def _discover_codex(panes: list[dict]) -> list[LiveSession]:
    state_db, logs_db = _codex_dbs()
    if not state_db or not logs_db:
        return []
    procs = _proc_snapshot()
    kids = _children(procs)
    best: dict[str, LiveSession] = {}
    for pane in panes:
        runtime_pid = _find_descendant(int(pane["pane_pid"]), procs, kids, _is_codex_proc)
        if runtime_pid is None:
            continue
        meta = _codex_meta_for_pid(runtime_pid, state_db, logs_db)
        if not meta or not meta.get("thread_id"):
            continue
        sid = f"codex:{meta['thread_id']}"
        updated_ms = meta.get("updated_at_ms")
        live = LiveSession(
            session_id=sid,
            pid=runtime_pid,
            cwd=(meta.get("cwd") or pane.get("cwd")),
            # Non-Claude live mapping gives us identity, not turn-state.
            status="shell",
            pane_id=str(pane["pane_id"]),
            version=meta.get("version"),
            updated_at=(
                datetime.fromtimestamp(float(updated_ms) / 1000, tz=timezone.utc)
                if updated_ms
                else None
            ),
        )
        prev = best.get(sid)
        if prev is None or (live.updated_at or datetime.min.replace(tzinfo=timezone.utc)) > (
            prev.updated_at or datetime.min.replace(tzinfo=timezone.utc)
        ):
            best[sid] = live
    return list(best.values())


def discover() -> list[LiveSession]:
    panes = tmux.list_panes()
    pane_pids = {p["pane_pid"]: p["pane_id"] for p in panes}
    out = _discover_claude(pane_pids) + _discover_codex(panes)
    out.sort(key=lambda s: s.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out
