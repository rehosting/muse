"""Session restore: snapshot the tmux topology (tagging each window with its resumable
Claude session id), and rebuild selected groups after a reboot — recreating windows that
run `claude --resume <id>`, skipping anything already live so restores never duplicate."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse.autopilot import snapshot as snap
from muse.models import LiveSession
from muse.routers import tmux as tmux_router


def pane(session, widx, pane_id, *, cmd="claude", cwd="/w", name="win", active=True, pidx=0):
    return {
        "session_name": session,
        "window_index": widx,
        "window_name": name,
        "window_active": active,
        "pane_id": pane_id,
        "pane_index": pidx,
        "pane_active": active,
        "command": cmd,
        "cwd": cwd,
        "title": "",
        "session_attached": True,
        "last_activity": 0,
        "window_id": "@" + pane_id[1:],
    }


# --- build_snapshot ---------------------------------------------------------------


def test_build_tags_windows_with_resumable_session_id(monkeypatch):
    panes = [
        pane("work", 0, "%1", cwd="/a", name="api"),
        pane("work", 1, "%2", cwd="/b", name="ui"),
        pane("exp", 0, "%3", cmd="bash", cwd="/c", name="shell"),
    ]
    live = [
        LiveSession(session_id="sid-a", pid=10, pane_id="%1", cwd="/a"),
        LiveSession(session_id="sid-b", pid=11, pane_id="%2", cwd="/b"),
    ]
    monkeypatch.setattr(snap.tmux, "available", lambda: True)
    monkeypatch.setattr(snap.tmux, "list_layout", lambda: panes)
    monkeypatch.setattr(snap.live_discovery, "discover", lambda: live)

    s = snap.build_snapshot()
    work = next(g for g in s["groups"] if g["name"] == "work")
    assert [w["session_id"] for w in work["windows"]] == ["sid-a", "sid-b"]
    assert all(w["kind"] == "claude" for w in work["windows"])
    exp = next(g for g in s["groups"] if g["name"] == "exp")
    assert exp["windows"][0]["kind"] == "shell" and exp["windows"][0]["session_id"] is None


def test_build_returns_none_without_claude(monkeypatch):
    monkeypatch.setattr(snap.tmux, "available", lambda: True)
    monkeypatch.setattr(snap.tmux, "list_layout", lambda: [pane("s", 0, "%1", cmd="bash")])
    monkeypatch.setattr(snap.live_discovery, "discover", lambda: [])
    assert snap.build_snapshot() is None


def test_topology_sig_ignores_ts_and_tracks_structure():
    a = {"ts": "T1", "groups": [{"name": "g", "windows": [
        {"window_name": "w", "cwd": "/x", "session_id": "s", "kind": "claude"}]}]}
    b = {**a, "ts": "T2"}  # only ts differs
    assert snap.topology_sig(a) == snap.topology_sig(b)
    c = json.loads(json.dumps(a))
    c["groups"][0]["windows"][0]["cwd"] = "/y"
    assert snap.topology_sig(c) != snap.topology_sig(a)


# --- annotate_liveness ------------------------------------------------------------


def _snap(*groups):
    return {"ts": "T", "groups": list(groups)}


def _g(name, *windows):
    return {"name": name, "windows": list(windows)}


def _w(name, sid, kind="claude", cwd="/w"):
    return {"window_name": name, "cwd": cwd, "command": "claude", "kind": kind, "session_id": sid}


def test_offer_true_when_claude_windows_none_live(monkeypatch):
    monkeypatch.setattr(snap, "_live_index", lambda: (set(), set(), set()))
    out = snap.annotate_liveness(_snap(_g("a", _w("x", "s1"), _w("y", "s2"))))
    assert out["offer"] is True and out["restorable_count"] == 2
    assert all(not w["live"] for g in out["groups"] for w in g["windows"])


def test_offer_false_when_a_session_is_live(monkeypatch):
    monkeypatch.setattr(snap, "_live_index", lambda: ({"s1"}, set(), set()))
    out = snap.annotate_liveness(_snap(_g("a", _w("x", "s1"), _w("y", "s2"))))
    assert out["offer"] is False  # not a clean reboot — something's still running
    live = {w["window_name"]: w["live"] for g in out["groups"] for w in g["windows"]}
    assert live == {"x": True, "y": False}


# --- restore ----------------------------------------------------------------------


@pytest.fixture
def restore_calls(monkeypatch, tmp_path):
    """Fresh tmux with nothing live; record new_session/new_window calls."""
    calls = {"session": [], "window": []}
    monkeypatch.setattr(snap, "_live_index", lambda: (set(), set(), set()))
    monkeypatch.setattr(
        snap.tmux, "new_session",
        lambda name, cwd=None, command=None, window_name=None:
        calls["session"].append((name, cwd, command, window_name)) or (True, ""),
    )
    monkeypatch.setattr(
        snap.tmux, "new_window",
        lambda cwd, command, session=None, name=None:
        calls["window"].append((cwd, command, session, name)) or (True, "%9"),
    )
    monkeypatch.setattr(snap.os.path, "isdir", lambda p: True)  # cwds exist
    return calls


def test_restore_creates_session_then_windows_resuming_claude(restore_calls):
    s = _snap(_g("work", _w("api", "sid-a", cwd="/a"), _w("ui", "sid-b", cwd="/b")))
    res = snap.restore(s, ["work"])
    assert res["restored"] == 2 and res["groups"] == ["work"]
    # First missing window creates the session (no leftover placeholder shell)...
    assert restore_calls["session"] == [("work", "/a", "claude --resume sid-a || claude --continue", "api")]
    # ...the rest append as windows.
    assert restore_calls["window"] == [("/b", "claude --resume sid-b || claude --continue", "work", "ui")]


def test_restore_skips_live_windows(monkeypatch, restore_calls):
    monkeypatch.setattr(snap, "_live_index", lambda: ({"sid-a"}, set(), {"work"}))
    s = _snap(_g("work", _w("api", "sid-a"), _w("ui", "sid-b")))
    res = snap.restore(s, ["work"])
    assert res["restored"] == 1 and res["skipped"] == 1
    # 'work' already exists (live), so the surviving window appends — no new session.
    assert restore_calls["session"] == []
    assert restore_calls["window"] == [("/w", "claude --resume sid-b || claude --continue", "work", "ui")]


def test_restore_shell_and_unknown_id_and_missing_cwd(monkeypatch, restore_calls):
    monkeypatch.setattr(snap.os.path, "isdir", lambda p: p == "/ok")
    s = _snap(_g(
        "g",
        _w("editor", None, kind="shell", cwd="/gone"),   # shell + missing cwd → ~ + bare shell
        _w("chat", None, kind="claude", cwd="/ok"),       # claude, no id → --continue
    ))
    snap.restore(s, ["g"])
    home = snap.os.path.expanduser("~")
    assert restore_calls["session"][0] == ("g", home, None, "editor")  # command None → default shell
    assert restore_calls["window"][0] == ("/ok", "claude --continue", "g", "chat")


# --- endpoints --------------------------------------------------------------------


class FakeStore:
    def __init__(self, snapshot=None):
        self._latest = (snap.topology_sig(snapshot), json.dumps(snapshot)) if snapshot else None
        self.entries = []

    def latest_snapshot(self):
        return self._latest

    def log(self, sid, action, detail=""):
        self.entries.append((sid, action, detail))


def _client(store, monkeypatch):
    app = FastAPI()
    app.include_router(tmux_router.router)
    app.state.autopilot = type("AP", (), {"store": store})()
    monkeypatch.setattr(tmux_router.tmux, "available", lambda: True)
    return TestClient(app)


def test_get_snapshot_endpoint_annotates(monkeypatch):
    monkeypatch.setattr(tmux_router.layout_snapshot, "_live_index", lambda: (set(), set(), set()))
    store = FakeStore(_snap(_g("a", _w("x", "s1"), _w("y", "s2"))))
    r = _client(store, monkeypatch).get("/api/tmux/snapshot")
    assert r.status_code == 200 and r.json()["offer"] is True

    empty = _client(FakeStore(None), monkeypatch).get("/api/tmux/snapshot")
    assert empty.json() == {"ts": None, "groups": [], "offer": False, "restorable_count": 0}


def test_restore_endpoint_rebuilds_selected(monkeypatch):
    monkeypatch.setattr(tmux_router.layout_snapshot, "_live_index", lambda: (set(), set(), set()))
    monkeypatch.setattr(tmux_router.layout_snapshot.os.path, "isdir", lambda p: True)
    made = []
    monkeypatch.setattr(
        tmux_router.layout_snapshot.tmux, "new_session",
        lambda name, cwd=None, command=None, window_name=None: made.append(name) or (True, ""),
    )
    store = FakeStore(_snap(_g("a", _w("x", "s1")), _g("b", _w("y", "s2"))))
    r = _client(store, monkeypatch).post("/api/tmux/restore", json={"groups": ["a"]})
    assert r.status_code == 200 and r.json()["restored"] == 1
    assert made == ["a"]  # only the selected group
    assert ("tmux", "layout_restore", "1 windows in ['a']") in store.entries


def test_restore_endpoint_400_without_snapshot(monkeypatch):
    r = _client(FakeStore(None), monkeypatch).post("/api/tmux/restore", json={"groups": ["a"]})
    assert r.status_code == 400
