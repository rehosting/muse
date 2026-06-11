"""Single-instance guard, pidfile, and version reporting."""

import os

import pytest

from muse import lifecycle
from muse.config import get_settings


@pytest.fixture
def muse_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "muse.db"))
    monkeypatch.delenv("MUSE_SINGLETON", raising=False)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_pidfile_roundtrip(muse_home):
    assert lifecycle.read_pidfile() is None
    lifecycle.write_pidfile(started_at=123.0)
    info = lifecycle.read_pidfile()
    assert info["pid"] == os.getpid()
    assert info["started_at"] == 123.0
    assert "version" in info
    lifecycle.remove_pidfile()
    assert lifecycle.read_pidfile() is None


def test_is_alive(muse_home):
    assert lifecycle.is_alive(os.getpid()) is True
    assert lifecycle.is_alive(0) is False
    # A pid that almost certainly doesn't exist.
    assert lifecycle.is_alive(2_000_000_000) is False


def test_ensure_single_instance_refuses_live(muse_home, monkeypatch):
    lifecycle.write_pidfile(started_at=1.0)  # pid is os.getpid() → alive
    with pytest.raises(SystemExit, match="already running"):
        lifecycle.ensure_single_instance()


def test_ensure_single_instance_ignores_stale(muse_home, monkeypatch):
    lifecycle.write_pidfile(started_at=1.0)
    monkeypatch.setattr(lifecycle, "is_alive", lambda pid: False)  # predecessor dead
    lifecycle.ensure_single_instance()  # should not raise


def test_ensure_single_instance_opt_out(muse_home, monkeypatch):
    lifecycle.write_pidfile(started_at=1.0)
    monkeypatch.setenv("MUSE_SINGLETON", "off")
    lifecycle.ensure_single_instance()  # opt-out → no refusal even with a live pidfile


def test_version_info_shape(muse_home):
    info = lifecycle.version_info(started_at=None)
    assert info["pid"] == os.getpid()
    assert "version" in info and "git_sha" in info and "code_mtime" in info
    assert "uptime_seconds" not in info and "stale" not in info
    timed = lifecycle.version_info(started_at=1.0)
    assert timed["uptime_seconds"] >= 0


def test_version_info_detects_stale(muse_home, monkeypatch):
    monkeypatch.setattr(lifecycle, "code_mtime", lambda: 1000.0)
    # Started long before the code changed → stale.
    assert lifecycle.version_info(started_at=10.0)["stale"] is True
    # Started after the code (with grace) → fresh.
    assert lifecycle.version_info(started_at=1000.0)["stale"] is False
