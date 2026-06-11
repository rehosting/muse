"""Shared SQLite setup for muse's own writable stores.

All of muse's writable state lives in ONE SQLite file (`~/.muse/muse.db`), opened
by several independent stores (annotations, notifications, investigations, search
index, autopilot). This module is the single place that decides HOW we connect, so
every store gets the same battle-tested PRAGMAs and the same lock-retry behaviour.

Why these choices (learned the hard way):
- `journal_mode=WAL` — many readers + one writer without blocking; required for the
  web UI, the alerts watcher, and MCP tool calls hitting the DB concurrently.
- `busy_timeout=5000` — wait on a held write lock instead of erroring immediately.
- `synchronous=NORMAL` — safe under WAL (only a power-loss/OS-crash can lose the last
  few commits, not a process crash); big fsync reduction for our write rate.
- `wal_autocheckpoint=1000` — ~4 MB backstop so the WAL can't run away (we once saw a
  462 MB WAL because nothing ever checkpointed and long readers blocked the default).

Transcripts stay strictly read-only — this is only for muse-owned DBs.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_BUSY_TIMEOUT_MS = 5000
_WAL_AUTOCHECKPOINT_PAGES = 1000


def connect(path: Path) -> sqlite3.Connection:
    """Open a muse.db connection with the standard PRAGMAs. `check_same_thread=False`
    because the FastAPI threadpool and background tasks share one connection per store
    (each store serialises its own access with a `threading.Lock`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA wal_autocheckpoint={_WAL_AUTOCHECKPOINT_PAGES}")
    return conn


def retry_locked(fn: Callable[[], T], tries: int = 8) -> T:
    """Run fn(), retrying on transient 'database is locked' (another writer on the
    shared muse.db) with a short escalating backoff before giving up. Re-raises any
    other OperationalError immediately. The caller's fn() must acquire/release its
    store lock INSIDE the call so each retry re-locks (threading.Lock is non-reentrant)."""
    for i in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:  # noqa: PERF203
            if "locked" in str(e).lower() and i < tries - 1:
                time.sleep(0.05 * (i + 1))
                continue
            raise
    raise AssertionError("unreachable")  # pragma: no cover


def checkpoint(conn: sqlite3.Connection, mode: str = "TRUNCATE") -> None:
    """Checkpoint (and, with TRUNCATE, shrink) the shared WAL. A long-lived reader can
    make this a no-op/busy — that's fine, it's best-effort; `wal_autocheckpoint` is the
    real backstop. Never raises."""
    try:
        conn.execute(f"PRAGMA wal_checkpoint({mode})")
    except sqlite3.OperationalError:
        pass
