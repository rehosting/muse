"""`muse` command-line entry point: start / stop / restart / status.

The canonical way to manage the server, so background launches can't pile up. The
app itself refuses to start alongside a live instance (see lifecycle.py); this CLI
is how you deliberately stop or replace one.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

from .config import get_settings
from .lifecycle import is_alive, pidfile_path, read_pidfile


def _running_pid() -> int | None:
    info = read_pidfile()
    if info and is_alive(int(info.get("pid", 0))):
        return int(info["pid"])
    return None


def _start() -> int:
    s = get_settings()
    # Friendly pre-check so the common case fails clean (the lifespan guard in
    # lifecycle.ensure_single_instance is the authoritative backstop for a direct
    # `uvicorn` launch, but it surfaces as a noisy traceback).
    pid = _running_pid()
    if pid is not None and os.environ.get("MUSE_SINGLETON", "").lower() != "off":
        print(
            f"muse is already running (pid {pid}). Use `muse restart` to replace it, "
            f"`muse stop` to stop it, or MUSE_SINGLETON=off to run a second instance.",
            file=sys.stderr,
        )
        return 1
    import uvicorn  # imported lazily so `status`/`stop` don't need it

    uvicorn.run("muse.main:app", host=s.host, port=s.port, log_level="warning")
    return 0


def _clear_pidfile_if(pid: int) -> None:
    """Remove the pidfile if it still points at `pid` (a SIGKILLed server can't run
    its own cleanup, so the CLI clears it — otherwise the next `start` sees a stale
    pidfile and refuses)."""
    info = read_pidfile()
    if info and info.get("pid") == pid:
        try:
            pidfile_path().unlink()
        except OSError:
            pass


def _stop(timeout: float = 10.0) -> int:
    pid = _running_pid()
    if pid is None:
        print("muse is not running.")
        info = read_pidfile()  # clear a stale pidfile left by a crash/kill
        if info:
            _clear_pidfile_if(int(info.get("pid", -1)))
        return 0
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_alive(pid):
            print(f"Stopped muse (pid {pid}).")
            _clear_pidfile_if(pid)
            return 0
        time.sleep(0.2)
    os.kill(pid, signal.SIGKILL)  # escalate if it didn't shut down cleanly
    # Wait for it to actually leave the process table so a following start doesn't
    # see it as briefly-alive and refuse.
    for _ in range(25):
        if not is_alive(pid):
            break
        time.sleep(0.2)
    _clear_pidfile_if(pid)
    print(f"Force-killed muse (pid {pid}) after {timeout:.0f}s.")
    return 0


def _restart() -> int:
    _stop()
    return _start()


def _status() -> int:
    s = get_settings()
    pid = _running_pid()
    if pid is None:
        print("muse: not running")
        return 1
    try:
        with urllib.request.urlopen(
            f"http://{s.host}:{s.port}/api/version", timeout=3
        ) as resp:
            info = json.loads(resp.read())
        flag = "  ⚠ STALE: running code predates the source — `muse restart`" if info.get("stale") else ""
        print(
            f"muse: running (pid {pid})  version={info.get('version')}  "
            f"git_sha={info.get('git_sha')}  uptime={info.get('uptime_seconds')}s{flag}"
        )
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        print(f"muse: pid {pid} alive but not answering on {s.host}:{s.port}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse", description="Manage the muse server.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("start", "stop", "restart", "status"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    return {"start": _start, "stop": _stop, "restart": _restart, "status": _status}[args.cmd]()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
