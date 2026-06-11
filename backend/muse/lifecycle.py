"""Process lifecycle: single-instance guard, pidfile, and code-version reporting.

Why this exists: muse is a long-running localhost server usually started in the
background. Without a guard, repeated launches pile up — we once had 3-4 stale
`uvicorn` processes alive at once, each holding ~/.muse/muse.db open, one pegged at
100% CPU running 18-hour-old code. Two defenses:

1. A pidfile + REFUSE-and-exit guard so a second instance can't quietly start
   alongside a live one (use `muse restart` to deliberately replace it).
2. `/api/version` reports the running code's version + git sha + uptime, so you can
   tell at a glance whether the live process is current or stale.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import __version__
from .config import get_settings


def pidfile_path() -> Path:
    """Colocated with the DB (~/.muse/muse.pid)."""
    return get_settings().db_path.parent / "muse.pid"


def is_alive(pid: int) -> bool:
    """True if a process with `pid` exists. `os.kill(pid, 0)` raises ESRCH if not,
    EPERM if it exists but isn't ours (treat as alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pidfile() -> Optional[dict]:
    try:
        return json.loads(pidfile_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_pidfile(started_at: float) -> None:
    info = {"pid": os.getpid(), "started_at": started_at, **code_version()}
    try:
        pidfile_path().write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        pass


def remove_pidfile() -> None:
    existing = read_pidfile()
    # Only remove if it's still ours — avoid clobbering a successor's pidfile.
    if existing and existing.get("pid") == os.getpid():
        try:
            pidfile_path().unlink()
        except OSError:
            pass


def ensure_single_instance() -> None:
    """Refuse to start if a live muse instance already holds the pidfile. A stale
    pidfile (dead pid) is ignored. Opt out with MUSE_SINGLETON=off."""
    if os.environ.get("MUSE_SINGLETON", "").lower() == "off":
        return
    existing = read_pidfile()
    if existing and is_alive(int(existing.get("pid", 0))):
        pid = existing.get("pid")
        ver = existing.get("git_sha") or existing.get("version") or "?"
        raise SystemExit(
            f"muse is already running (pid {pid}, code {ver}). "
            f"Use `muse restart` to replace it, `muse stop` to stop it, or set "
            f"MUSE_SINGLETON=off to run a second instance."
        )


@lru_cache(maxsize=1)
def code_version() -> dict:
    """{version, git_sha} for the RUNNING code. git_sha comes from a build-stamp env
    (MUSE_GIT_SHA) if set, else `git` in the muse repo dir — NEVER the cwd, which is
    the user's project, not muse. None if unavailable (e.g. a wheel with no .git)."""
    sha = os.environ.get("MUSE_GIT_SHA") or _git_sha()
    return {"version": __version__, "git_sha": sha}


def _git_sha() -> Optional[str]:
    repo_dir = Path(__file__).resolve().parents[2]  # backend/muse/lifecycle.py -> repo root
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def code_mtime() -> float:
    """Newest mtime among the package's .py files = when the on-disk code last
    changed. Compared against the process start time, this reveals a STALE server
    (running code older than the source) without needing git — the exact failure
    we hit (an 18h-old process serving edited code). Computed live, not cached."""
    pkg = Path(__file__).resolve().parent
    newest = 0.0
    for py in pkg.rglob("*.py"):
        try:
            newest = max(newest, py.stat().st_mtime)
        except OSError:
            continue
    return newest


def version_info(started_at: Optional[float] = None) -> dict:
    info = {"pid": os.getpid(), **code_version()}
    cm = code_mtime()
    info["code_mtime"] = round(cm, 1)
    if started_at is not None:
        info["started_at"] = started_at
        info["uptime_seconds"] = round(time.time() - started_at, 1)
        # 2s grace so a normal startup (import after the files settle) isn't "stale".
        info["stale"] = started_at + 2.0 < cm
    return info
