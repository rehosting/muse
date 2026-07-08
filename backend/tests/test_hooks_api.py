"""Hook ingestion endpoint + event dispatch (fast: all delays zeroed)."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse.routers import hooks as hooks_router


class FakeAlerts:
    def __init__(self):
        self.calls = []

    def hook_user_replied(self, sid):
        self.calls.append(("user_replied", sid))

    async def hook_needs_you(self, sid, message):
        self.calls.append(("needs_you", sid, message))

    async def hook_turn_ended(self, sid):
        self.calls.append(("turn_ended", sid))

    async def hook_session_end(self, sid):
        self.calls.append(("session_end", sid))


def _state(delivered):
    return SimpleNamespace(
        alerts=FakeAlerts(),
        autopilot=SimpleNamespace(deliver_queued=lambda sid: delivered),
        hook_events=None,
    )


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hooks_router, "_QUEUE_DELIVER_DELAY", 0.0)


def _dispatch(state, event, sid="a" * 36, payload=None):
    asyncio.run(hooks_router._dispatch(state, event, sid, payload or {}))


def test_stop_delivers_queue_and_skips_alert():
    state = _state(delivered=[7])
    _dispatch(state, "Stop")
    assert state.alerts.calls == [("user_replied", "a" * 36)]


def test_stop_with_empty_queue_alerts():
    state = _state(delivered=[])
    _dispatch(state, "Stop")
    assert state.alerts.calls == [("turn_ended", "a" * 36)]


def test_notification_and_prompt_and_end():
    state = _state(delivered=[])
    _dispatch(state, "Notification", payload={"message": "needs permission for Bash"})
    _dispatch(state, "UserPromptSubmit")
    _dispatch(state, "SessionEnd")
    _dispatch(state, "SubagentStop")  # recorded upstream, no action
    assert state.alerts.calls == [
        ("needs_you", "a" * 36, "needs permission for Bash"),
        ("user_replied", "a" * 36),
        ("session_end", "a" * 36),
    ]


def test_endpoint_records_and_never_errors(monkeypatch):
    app = FastAPI()
    app.include_router(hooks_router.router)
    dispatched = []

    async def fake_dispatch(state, event, sid, payload):
        dispatched.append((event, sid))

    monkeypatch.setattr(hooks_router, "_dispatch", fake_dispatch)
    app.state.alerts = FakeAlerts()
    app.state.autopilot = SimpleNamespace(deliver_queued=lambda sid: [])
    c = TestClient(app)

    sid = "0f0e0d0c-1111-2222-3333-444455556666"
    r = c.post("/api/hooks/claude", json={"hook_event_name": "Stop", "session_id": sid})
    assert r.status_code == 200 and r.json()["ok"] is True

    # malformed payloads are acknowledged, not errored (the relay must never break)
    r = c.post("/api/hooks/claude", content=b"not json",
               headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] is False
    r = c.post("/api/hooks/claude", json={"session_id": "../../etc/passwd",
                                          "hook_event_name": "Stop"})
    assert r.status_code == 200  # bad sid: recorded but not dispatched

    st = c.get("/api/hooks/status").json()
    assert st["events_seen"] == 2  # the two parseable payloads
    assert st["last_event_at"] is not None
    assert dispatched == [("Stop", sid)]
