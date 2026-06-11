"""Shared SQLite setup: PRAGMAs, lock-retry, and checkpoint helpers."""

import sqlite3

import pytest

from muse import db


def test_connect_sets_pragmas(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 1000
    finally:
        conn.close()


def test_connect_creates_parent_dir(tmp_path):
    nested = tmp_path / "a" / "b" / "muse.db"
    conn = db.connect(nested)
    try:
        assert nested.parent.is_dir()
    finally:
        conn.close()


def test_retry_locked_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert db.retry_locked(flaky, tries=5) == "ok"
    assert calls["n"] == 3


def test_retry_locked_reraises_non_lock_error():
    def boom():
        raise sqlite3.OperationalError("no such table: nope")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db.retry_locked(boom, tries=3)


def test_retry_locked_gives_up_after_tries():
    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        db.retry_locked(always_locked, tries=2)


def test_checkpoint_is_safe(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    try:
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        db.checkpoint(conn)  # should not raise
        db.checkpoint(conn, "PASSIVE")
    finally:
        conn.close()
