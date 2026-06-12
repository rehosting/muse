"""Tests for the respond-from-muse endpoints (fake tmux + fake live discovery)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse.models import LiveSession
from muse.routers import interact


class FakeStore:
    def __init__(self):
        self.entries = []

    def log(self, sid, action, detail=""):
        self.entries.append((sid, action, detail))

    def recent_log_for(self, sid, limit=20):
        return []


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(interact.router)
    app.state.autopilot = type("AP", (), {"store": FakeStore()})()

    sent: list[tuple] = []
    monkeypatch.setattr(
        interact.live_discovery,
        "discover",
        lambda: [LiveSession(session_id="live1", pid=1, status="idle", pane_id="%9"),
                 LiveSession(session_id="nopane", pid=2, status="idle", pane_id=None)],
    )
    monkeypatch.setattr(interact.tmux, "send_text", lambda p, t, submit=True: (sent.append(("text", p, t)) or (True, "")))
    monkeypatch.setattr(interact.tmux, "send_key", lambda p, k: (sent.append(("key", p, k)) or (True, "")))
    monkeypatch.setattr(interact.tmux, "accept_suggestion", lambda p: (sent.append(("accept", p)) or (True, "")))
    monkeypatch.setattr(interact.tmux, "capture_pane", lambda p, n: "PANE CONTENT")
    c = TestClient(app)
    c.sent = sent
    c.store = app.state.autopilot.store
    return c


def test_respond_success_logs_user_send(client):
    r = client.post("/api/sessions/live1/respond", json={"text": "carry on"})
    assert r.status_code == 200 and r.json() == {"ok": True, "pane_id": "%9"}
    assert ("text", "%9", "carry on") in client.sent
    assert client.store.entries[0][:2] == ("live1", "user_send")


def test_respond_no_live_process(client):
    r = client.post("/api/sessions/ghost/respond", json={"text": "x"})
    assert r.status_code == 400
    assert "no live process" in r.json()["detail"]


def test_respond_no_pane(client):
    r = client.post("/api/sessions/nopane/respond", json={"text": "x"})
    assert r.status_code == 400
    assert "no tmux pane matched" in r.json()["detail"]


def test_respond_empty_text(client):
    r = client.post("/api/sessions/live1/respond", json={"text": "   "})
    assert r.status_code == 400


def test_keys_whitelist(client):
    assert client.post("/api/sessions/live1/keys", json={"key": "escape"}).status_code == 200
    assert ("key", "%9", "Escape") in client.sent
    assert client.post("/api/sessions/live1/keys", json={"key": "accept"}).status_code == 200
    assert ("accept", "%9") in client.sent
    r = client.post("/api/sessions/live1/keys", json={"key": "C-c"})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]


def test_terminal_peek(client):
    r = client.get("/api/sessions/live1/terminal")
    assert r.status_code == 200 and r.json()["text"] == "PANE CONTENT"
    assert client.get("/api/sessions/ghost/terminal").status_code == 400
