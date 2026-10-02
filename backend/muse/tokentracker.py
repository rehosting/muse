"""Surface `tokentracker-cli`'s session analytics inside muse.

muse already derives its own usage numbers (usage_cache/stats/runway) from the raw
transcripts. This module doesn't compete with that — it shells out to the tokentracker
CLI and hands its JSON through, because that tool's per-session accounting (edit turns,
first-pass rate, subagent attribution, cost) is better than ours and reimplementing it
would just be a worse copy that drifts.

Two speeds, which is the whole reason for the caching here:
  * default   — the CLI reads its own cache under ~/.tokentracker (~0.2s), cheap enough
                to serve on page load.
  * refresh   — `--refresh` re-parses the transcripts (~12s), so it's an explicit user
                action, never a poll.

`--no-git` is always passed: the CLI's git-outcome enrichment spawns git with each
session's recorded cwd and dies with ENOTDIR when any of them is no longer a directory,
which takes the whole export down.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import date, timedelta
from typing import Any, Optional

# How long a parsed export stays good before we shell out again.
CACHE_TTL_SECONDS = 60.0
REFRESH_CACHE_TTL_SECONDS = 300.0
# Wall-clock ceilings for the subprocess.
TIMEOUT_SECONDS = 45
REFRESH_TIMEOUT_SECONDS = 240

_PACKAGE = "tokentracker-cli"
_lock = threading.Lock()
_cache: dict[tuple[int, bool], tuple[float, dict]] = {}


class TrackerError(RuntimeError):
    """The CLI is missing, timed out, or returned something that isn't JSON."""


def _npx_cache_binary() -> Optional[str]:
    """The tracker already unpacked in npm's _npx cache, if it's there. Cheaper and
    more predictable than letting `npx` decide to hit the network mid-request."""
    pattern = os.path.expanduser(f"~/.npm/_npx/*/node_modules/{_PACKAGE}/bin/tracker.js")
    return next(iter(sorted(glob.glob(pattern))), None)


def resolve_command() -> list[str]:
    """How to invoke the tracker, most-explicit first.

    MUSE_TOKENTRACKER_CMD wins (a full command line, shell-split), then a `tokentracker`
    on PATH, then the npx cache, and only as a last resort `npx -y` — which may download.
    """
    override = os.environ.get("MUSE_TOKENTRACKER_CMD", "").strip()
    if override:
        return override.split()
    on_path = shutil.which("tokentracker") or shutil.which("tokentracker-cli")
    if on_path:
        return [on_path]
    cached = _npx_cache_binary()
    node = shutil.which("node")
    if cached and node:
        return [node, cached]
    npx = shutil.which("npx")
    if npx:
        return [npx, "-y", _PACKAGE]
    raise TrackerError(
        "tokentracker CLI not found — install it (npm i -g tokentracker-cli) "
        "or set MUSE_TOKENTRACKER_CMD"
    )


def _run(days: int, refresh: bool) -> dict[str, Any]:
    today = date.today()
    args = resolve_command() + [
        "sessions",
        "--from", (today - timedelta(days=max(0, days - 1))).isoformat(),
        "--to", today.isoformat(),
        "--format", "json",
        "--no-git",  # see module docstring: git enrichment crashes on stale cwds
    ]
    if refresh:
        args.append("--refresh")
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=REFRESH_TIMEOUT_SECONDS if refresh else TIMEOUT_SECONDS,
            cwd=os.path.expanduser("~"),  # a stable, always-present cwd
        )
    except subprocess.TimeoutExpired as exc:
        raise TrackerError(f"tokentracker timed out after {exc.timeout}s") from exc
    except OSError as exc:
        raise TrackerError(f"could not run tokentracker: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise TrackerError(f"tokentracker exited {proc.returncode}: {detail[-1] if detail else ''}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise TrackerError(f"tokentracker returned non-JSON output: {exc}") from exc


def get_usage(days: int = 7, refresh: bool = False, force: bool = False) -> dict[str, Any]:
    """Parsed tracker export for the last `days` days, TTL-cached per (days, refresh).

    The lock is held across the subprocess on purpose: a 12s `--refresh` that two page
    loads request at once should run once, not twice.
    """
    key = (days, refresh)
    ttl = REFRESH_CACHE_TTL_SECONDS if refresh else CACHE_TTL_SECONDS
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and not force and now - hit[0] < ttl:
            return hit[1]
        data = _run(days, refresh)
        _cache[key] = (time.monotonic(), data)
        return data


def clear_cache() -> None:
    with _lock:
        _cache.clear()
