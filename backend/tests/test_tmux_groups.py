"""Groups = tmux sessions: move whole windows between sessions, create/rename/kill
sessions. tmux.move_window computes an append index from the live layout; the router
validates window ids and group names before shelling out."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from muse.autopilot import tmux as tmux_mod
from muse.routers import tmux as tmux_router


# --- tmux.move_window (pure-ish; list_layout + _run are the only I/O) -------------


def _layout():
    return [
        {"session_name": "a", "window_index": 0, "window_id": "@5"},
        {"session_name": "b", "window_index": 0, "window_id": "@1"},
        {"session_name": "b", "window_index": 3, "window_id": "@2"},
    ]


def test_move_window_appends_after_last_index(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_mod, "list_layout", _layout)
    monkeypatch.setattr(tmux_mod, "_run", lambda args, **kw: calls.append(args) or (0, "", ""))
    ok, err = tmux_mod.move_window("@5", "b")
    assert ok and err == ""
    assert calls == [["move-window", "-s", "@5", "-t", "b:4"]]  # max(0,3)+1


def test_move_window_noop_when_already_in_dst(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_mod, "list_layout", _layout)
    monkeypatch.setattr(tmux_mod, "_run", lambda args, **kw: calls.append(args) or (0, "", ""))
    ok, err = tmux_mod.move_window("@1", "b")  # @1 already lives in b
    assert ok and err == "" and calls == []


def test_move_window_unknown_targets(monkeypatch):
    monkeypatch.setattr(tmux_mod, "list_layout", _layout)
    monkeypatch.setattr(tmux_mod, "_run", lambda args, **kw: (0, "", ""))
    assert tmux_mod.move_window("@99", "b")[0] is False  # window not found
    assert tmux_mod.move_window("@5", "ghost")[0] is False  # session not found


def test_rename_window_disables_auto_rename_then_renames(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_mod, "_run", lambda args, **kw: calls.append(args) or (0, "", ""))
    ok, err = tmux_mod.rename_window("@5", "api-refactor")
    assert ok and err == ""
    assert calls == [
        ["set-window-option", "-t", "@5", "automatic-rename", "off"],
        ["rename-window", "-t", "@5", "api-refactor"],
    ]


def test_infer_provider_prefers_start_command_then_window_name():
    assert tmux_router._infer_provider("node", "codex", "scratch") == "codex"
    assert tmux_router._infer_provider("python", "antigravity --resume", "scratch") == "gemini"
    assert tmux_router._infer_provider("bash", "", "OpenCode") == "opencode"


# --- router endpoints -------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.entries = []

    def log(self, sid, action, detail=""):
        self.entries.append((sid, action, detail))


class FakeService:
    def __init__(self):
        self.created_packs = []

    def create_pack(
        self,
        source_session_id,
        include_brief=True,
        note_ids=None,
        include_files=True,
        extra_md="",
        title="",
    ):
        self.created_packs.append(
            {
                "source_session_id": source_session_id,
                "include_brief": include_brief,
                "note_ids": note_ids,
                "include_files": include_files,
                "extra_md": extra_md,
                "title": title,
            }
        )
        return SimpleNamespace(id="pk_test", path="/tmp/pk_test.md")

    def refresh_sessions_soon(self):
        return None


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(tmux_router.router)
    app.state.autopilot = type("AP", (), {"store": FakeStore()})()
    app.state.service = FakeService()

    calls: list[tuple] = []
    monkeypatch.setattr(tmux_router.tmux, "available", lambda: True)
    monkeypatch.setattr(
        tmux_router.tmux, "new_session", lambda n: (calls.append(("new", n)) or (True, ""))
    )
    monkeypatch.setattr(
        tmux_router.tmux, "rename_session", lambda o, n: (calls.append(("rename", o, n)) or (True, ""))
    )
    monkeypatch.setattr(
        tmux_router.tmux, "kill_session", lambda n: (calls.append(("kill", n)) or (True, ""))
    )
    monkeypatch.setattr(
        tmux_router.tmux, "move_window", lambda w, s: (calls.append(("move", w, s)) or (True, ""))
    )
    monkeypatch.setattr(
        tmux_router.tmux, "rename_window", lambda w, n: (calls.append(("winrename", w, n)) or (True, ""))
    )
    monkeypatch.setattr(
        tmux_router.tmux, "kill_window", lambda w: (calls.append(("killwin", w)) or (True, ""))
    )
    monkeypatch.setattr(
        tmux_router.tmux, "send_text", lambda p, t, submit=True: (calls.append(("send", p, t, submit)) or (True, ""))
    )
    monkeypatch.setattr(
        tmux_router.tmux,
        "new_window",
        lambda cwd, command, session=None, name=None: (
            calls.append(("new-window", cwd, command, session, name)) or (True, "%99")
        ),
    )
    # A window @7 whose active pane sits in a cwd claimed by a cleanup profile.
    monkeypatch.setattr(
        tmux_router.tmux,
        "list_layout",
        lambda: [
            {"window_id": "@7", "pane_active": True, "cwd": "/proj/stuff", "window_name": "stuff"}
        ],
    )
    c = TestClient(app)
    c.calls = calls
    return c


def test_close_window_kills_only_without_cleanup(client, monkeypatch):
    monkeypatch.setattr(
        tmux_router.profiles_mod, "match_cleanup", lambda cwd, name: {"command": "x", "run_cwd": "/"}
    )
    ran = []
    monkeypatch.setattr(
        tmux_router.profiles_mod, "run_cleanup", lambda c, d: ran.append(c) or (True, "")
    )
    r = client.post("/api/tmux/windows/@7/close", json={"cleanup": False})
    assert r.status_code == 200 and r.json()["cleanup_ran"] is False
    assert ("killwin", "@7") in client.calls and ran == []  # no cleanup when not opted in


def test_pane_send_preserves_literal_spaces_when_not_submitting(client):
    r = client.post("/api/tmux/panes/%252512/send", json={"text": " ", "submit": False})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert ("send", "%12", " ", False) in client.calls


def test_pane_send_rejects_blank_submitted_prompt(client):
    r = client.post("/api/tmux/panes/%252512/send", json={"text": "   ", "submit": True})
    assert r.status_code == 400


def test_close_window_runs_cleanup_after_kill(client, monkeypatch):
    monkeypatch.setattr(
        tmux_router.profiles_mod,
        "match_cleanup",
        lambda cwd, name: {"command": "./do_worktree.sh remove stuff", "run_cwd": "/proj"},
    )
    ran = []
    monkeypatch.setattr(
        tmux_router.profiles_mod, "run_cleanup", lambda c, d: ran.append((c, d)) or (True, "removed")
    )
    r = client.post("/api/tmux/windows/@7/close", json={"cleanup": True})
    body = r.json()
    assert body["cleanup_ran"] and body["cleanup_ok"] and body["cleanup_output"] == "removed"
    assert ("killwin", "@7") in client.calls
    assert ran == [("./do_worktree.sh remove stuff", "/proj")]


def test_close_window_rejects_bad_window_id(client):
    r = client.post("/api/tmux/windows/nope/close", json={"cleanup": False})
    assert r.status_code == 400 and client.calls == []


def test_window_cleanup_preview(client, monkeypatch):
    monkeypatch.setattr(
        tmux_router.profiles_mod,
        "match_cleanup",
        lambda cwd, name: {"profile": "igloo", "command": "rm stuff", "run_cwd": "/proj"},
    )
    r = client.get("/api/tmux/windows/@7/cleanup")
    assert r.status_code == 200
    assert r.json() == {
        "window_name": "stuff",
        "cwd": "/proj/stuff",
        "cleanup": {"profile": "igloo", "command": "rm stuff"},
    }


def test_create_group_ok(client):
    r = client.post("/api/tmux/sessions", json={"name": "work"})
    assert r.status_code == 200 and r.json()["session"] == "work"
    assert ("new", "work") in client.calls


@pytest.mark.parametrize("bad", ["a.b", "a:b", "with space", "", "x" * 41])
def test_create_group_rejects_bad_names(client, bad):
    r = client.post("/api/tmux/sessions", json={"name": bad})
    assert r.status_code == 400
    assert client.calls == []  # never reached tmux


def test_rename_group(client):
    r = client.post("/api/tmux/sessions/work/rename", json={"name": "play"})
    assert r.status_code == 200 and r.json()["session"] == "play"
    assert ("rename", "work", "play") in client.calls


def test_delete_group(client):
    r = client.request("DELETE", "/api/tmux/sessions/work")
    assert r.status_code == 200
    assert ("kill", "work") in client.calls


def test_move_window_endpoint(client):
    r = client.post("/api/tmux/windows/@7/move", json={"session": "work"})
    assert r.status_code == 200 and r.json() == {"ok": True, "window_id": "@7", "session": "work"}
    assert ("move", "@7", "work") in client.calls


def test_move_window_rejects_bad_window_id(client):
    r = client.post("/api/tmux/windows/nope/move", json={"session": "work"})
    assert r.status_code == 400 and client.calls == []


def test_rename_window_endpoint(client):
    r = client.post("/api/tmux/windows/@7/rename", json={"name": "  api-refactor  "})
    assert r.status_code == 200 and r.json()["name"] == "api-refactor"  # trimmed
    assert ("winrename", "@7", "api-refactor") in client.calls


@pytest.mark.parametrize("bad", ["", "   ", "a\nb", "x" * 101])
def test_rename_window_rejects_bad_names(client, bad):
    r = client.post("/api/tmux/windows/@7/rename", json={"name": bad})
    assert r.status_code == 400 and client.calls == []


def test_rename_window_rejects_bad_window_id(client):
    r = client.post("/api/tmux/windows/nope/rename", json={"name": "x"})
    assert r.status_code == 400 and client.calls == []


def test_launch_codex_from_codex_session_forks_in_place(client, monkeypatch):
    monkeypatch.setattr(tmux_router.os.path, "isdir", lambda path: path == "/proj/stuff")
    monkeypatch.setattr(
        tmux_router.profiles_mod,
        "find_profile",
        lambda name: SimpleNamespace(name="Codex", cwd="~", command="codex", params=[]),
    )
    r = client.post(
        "/api/tmux/codex/launch",
        json={
            "source_session_id": "codex:019f44ea-d2c3-7df1-9d1c-b240b4e748d8",
            "cwd": "/proj/stuff",
            "session": "work",
            "window_name": "muse",
        },
    )
    assert r.status_code == 200
    assert client.app.state.service.created_packs == []
    assert client.calls[-1] == (
        "new-window",
        "/proj/stuff",
        "codex fork 019f44ea-d2c3-7df1-9d1c-b240b4e748d8",
        "work",
        "muse codex",
    )


def test_launch_codex_from_other_session_builds_reference_pack(client, monkeypatch):
    monkeypatch.setattr(tmux_router.os.path, "isdir", lambda path: path == "/proj/stuff")
    monkeypatch.setattr(
        tmux_router.profiles_mod,
        "find_profile",
        lambda name: SimpleNamespace(name="Codex", cwd="~", command="codex --search", params=[]),
    )
    r = client.post(
        "/api/tmux/codex/launch",
        json={
            "source_session_id": "sess-1",
            "cwd": "/proj/stuff",
            "session": "work",
            "window_name": "muse",
        },
    )
    assert r.status_code == 200
    assert client.app.state.service.created_packs == [
        {
            "source_session_id": "sess-1",
            "include_brief": True,
            "note_ids": None,
            "include_files": True,
            "extra_md": "",
            "title": "",
        }
    ]
    assert client.calls[-1] == (
        "new-window",
        "/proj/stuff",
        "codex --search 'Read /tmp/pk_test.md for context from my previous session'",
        "work",
        "muse codex",
    )
