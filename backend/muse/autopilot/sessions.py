"""Discover active Claude Code sessions and match them to tmux panes.

Source of truth is ~/.claude/sessions/{pid}.json (written by Claude Code),
which gives pid -> sessionId/cwd/status. We keep only live pids, dedupe by
sessionId, and map each to its tmux pane via process ancestry.
"""

from __future__ import annotations

import glob
import json
import os
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


def discover() -> list[LiveSession]:
    sessions_dir = get_settings().claude_dir / "sessions"
    if not sessions_dir.is_dir():
        return []

    pane_pids = {p["pane_pid"]: p["pane_id"] for p in tmux.list_panes()}

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
    out.sort(key=lambda s: s.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out
