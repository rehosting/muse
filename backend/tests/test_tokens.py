"""Token-usage page data: CLI resolution, caching, and the endpoint's failure modes."""

import json
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse import tokentracker as tt
from muse.routers import tokens as tokens_router

EXPORT = {
    "available": True,
    "session_count": 2,
    "summary": {"sessions": 2, "cost_usd": 12.5, "total_tokens": 1000},
    "by_model": [{"model": "claude-opus-5", "sessions": 2, "cost_usd": 12.5}],
    "subagents": [],
    "sessions": [{"session_hash": "abc", "source": "claude", "project_key": "muse"}],
    "provenance": {"privacy": "metadata-only"},
}


@pytest.fixture(autouse=True)
def clear():
    tt.clear_cache()
    yield
    tt.clear_cache()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(tokens_router.router)
    return TestClient(app)


def fake_run(calls, *, stdout=None, code=0, exc=None):
    def _run(args, **kw):
        calls.append(args)
        if exc:
            raise exc
        return subprocess.CompletedProcess(
            args, code, json.dumps(EXPORT) if stdout is None else stdout, ""
        )

    return _run


# --- invocation -------------------------------------------------------------------


def test_always_passes_no_git(monkeypatch):
    """Git enrichment spawns git with each session's recorded cwd and dies ENOTDIR when
    one of them no longer exists — it must never be enabled."""
    calls = []
    monkeypatch.setattr(tt.subprocess, "run", fake_run(calls))
    tt.get_usage(days=7)
    assert "--no-git" in calls[0]
    assert "--refresh" not in calls[0]
    assert "--format" in calls[0] and "json" in calls[0]


def test_refresh_adds_the_flag_and_uses_a_longer_timeout(monkeypatch):
    calls, timeouts = [], []

    def _run(args, **kw):
        calls.append(args)
        timeouts.append(kw.get("timeout"))
        return subprocess.CompletedProcess(args, 0, json.dumps(EXPORT), "")

    monkeypatch.setattr(tt.subprocess, "run", _run)
    tt.get_usage(days=7, refresh=True)
    assert "--refresh" in calls[0]
    assert timeouts[0] == tt.REFRESH_TIMEOUT_SECONDS


def test_env_override_wins_over_everything(monkeypatch):
    monkeypatch.setenv("MUSE_TOKENTRACKER_CMD", "/opt/tt --flag")
    assert tt.resolve_command()[:2] == ["/opt/tt", "--flag"]


def test_missing_cli_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("MUSE_TOKENTRACKER_CMD", raising=False)
    monkeypatch.setattr(tt.shutil, "which", lambda _n: None)
    monkeypatch.setattr(tt, "_npx_cache_binary", lambda: None)
    with pytest.raises(tt.TrackerError, match="not found"):
        tt.resolve_command()


# --- caching ----------------------------------------------------------------------


def test_repeat_calls_do_not_reshell(monkeypatch):
    calls = []
    monkeypatch.setattr(tt.subprocess, "run", fake_run(calls))
    tt.get_usage(days=7)
    tt.get_usage(days=7)
    assert len(calls) == 1  # TTL cache — the page polling must not spawn per request


def test_cache_is_keyed_by_window_and_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(tt.subprocess, "run", fake_run(calls))
    tt.get_usage(days=7)
    tt.get_usage(days=30)
    tt.get_usage(days=7, refresh=True)
    assert len(calls) == 3  # a 30-day view must not be served 7-day numbers


def test_force_bypasses_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(tt.subprocess, "run", fake_run(calls))
    tt.get_usage(days=7)
    tt.get_usage(days=7, force=True)
    assert len(calls) == 2


# --- failure modes ----------------------------------------------------------------


def test_non_zero_exit_becomes_a_tracker_error(monkeypatch):
    monkeypatch.setattr(tt.subprocess, "run", fake_run([], stdout="boom", code=1))
    with pytest.raises(tt.TrackerError, match="exited 1"):
        tt.get_usage(days=7)


def test_non_json_output_becomes_a_tracker_error(monkeypatch):
    monkeypatch.setattr(tt.subprocess, "run", fake_run([], stdout="not json"))
    with pytest.raises(tt.TrackerError, match="non-JSON"):
        tt.get_usage(days=7)


def test_timeout_becomes_a_tracker_error(monkeypatch):
    monkeypatch.setattr(
        tt.subprocess, "run",
        fake_run([], exc=subprocess.TimeoutExpired(cmd="tt", timeout=45)),
    )
    with pytest.raises(tt.TrackerError, match="timed out"):
        tt.get_usage(days=7)


# --- endpoint ---------------------------------------------------------------------


def test_endpoint_returns_the_tool_payload_in_an_envelope(client, monkeypatch):
    monkeypatch.setattr(tt.subprocess, "run", fake_run([]))
    body = client.get("/api/tokens?days=7").json()
    assert body["days"] == 7 and body["refreshed"] is False and body["available"] is True
    assert body["summary"]["cost_usd"] == 12.5
    assert body["by_model"][0]["model"] == "claude-opus-5"
    assert body["generated_at"]


def test_endpoint_rejects_an_unbounded_window(client):
    r = client.get("/api/tokens?days=100000")
    assert r.status_code == 400  # don't let a query chew through every transcript


def test_missing_tool_is_503_not_500(client, monkeypatch):
    def _boom(*a, **k):
        raise tt.TrackerError("tokentracker CLI not found")

    monkeypatch.setattr(tt, "get_usage", _boom)
    r = client.get("/api/tokens")
    assert r.status_code == 503 and "not found" in r.json()["detail"]
