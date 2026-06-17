"""`muse` command-line entry point: start / stop / restart / status.

The canonical way to manage the server, so background launches can't pile up. The
app itself refuses to start alongside a live instance (see lifecycle.py); this CLI
is how you deliberately stop or replace one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
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


def _pids_on_port(port: int) -> list[int]:
    """PIDs listening on `port`, straight from the kernel — independent of the
    pidfile. This is the backstop that lets stop/restart kill a stale instance
    that's still holding the port under a different pid (the failure mode where a
    pegged server ignored SIGTERM and `muse restart` left it serving old code)."""
    try:
        out = subprocess.run(
            ["ss", "-ltnpH", f"sport = :{port}"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return []
    return sorted({int(p) for p in re.findall(r"pid=(\d+)", out)})


def _kill(pid: int, timeout: float = 10.0) -> bool:
    """SIGTERM, then SIGKILL if it won't leave within `timeout`. Returns True once
    the pid is gone (a 100%-CPU event loop can't service a graceful shutdown, so
    the escalation to SIGKILL is what actually frees the port)."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    for _ in range(25):
        if not is_alive(pid):
            return True
        time.sleep(0.2)
    return not is_alive(pid)


def _start() -> int:
    s = get_settings()
    # Friendly pre-check so the common case fails clean (the lifespan guard in
    # lifecycle.ensure_single_instance is the authoritative backstop for a direct
    # `uvicorn` launch, but it surfaces as a noisy traceback).
    if os.environ.get("MUSE_SINGLETON", "").lower() != "off":
        pid = _running_pid()
        # Also refuse if the port is held by a process the pidfile doesn't know
        # about (stale instance, removed pidfile) — otherwise uvicorn would either
        # collide on bind or, worse, a second instance would quietly coexist.
        port_holders = [p for p in _pids_on_port(s.port) if p != os.getpid()]
        blocker = pid if pid is not None else (port_holders[0] if port_holders else None)
        if blocker is not None:
            print(
                f"muse is already running (pid {blocker}). Use `muse restart` to replace it, "
                f"`muse stop` to stop it, or MUSE_SINGLETON=off to run a second instance.",
                file=sys.stderr,
            )
            return 1
    import uvicorn  # imported lazily so `status`/`stop` don't need it

    # http="h11" (not the default httptools): httptools busy-loops at ~100% CPU on
    # half-closed (CLOSE-WAIT) connections — which port probes, dropped SSE/MCP
    # streams, and proxies all leave behind — pegging the event loop indefinitely.
    # h11 handles the half-close correctly. Throughput is irrelevant for a local
    # single-user tool; correctness isn't.
    uvicorn.run("muse.main:app", host=s.host, port=s.port, log_level="warning", http="h11")
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
    s = get_settings()
    killed: list[int] = []
    pid = _running_pid()
    if pid is not None:
        _kill(pid, timeout)
        _clear_pidfile_if(pid)
        killed.append(pid)
    else:
        info = read_pidfile()  # clear a stale pidfile left by a crash/kill
        if info:
            _clear_pidfile_if(int(info.get("pid", -1)))
    # Backstop: kill whatever still holds the port. This is the case the old
    # pidfile-only stop missed — a stale/pegged instance under a different pid
    # kept the port and `muse restart` silently failed to replace it.
    for ppid in _pids_on_port(s.port):
        if ppid != os.getpid() and ppid not in killed:
            _kill(ppid, timeout)
            _clear_pidfile_if(ppid)
            killed.append(ppid)
    if not killed:
        print("muse is not running.")
    elif _pids_on_port(s.port):
        print(f"⚠ tried to stop {killed} but port {s.port} is still held.")
        return 1
    else:
        print(f"Stopped muse (pid {', '.join(map(str, killed))}).")
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
