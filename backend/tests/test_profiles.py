"""Launch profiles: hand-authored templates (~/.muse/profiles.toml) for opening a new
tmux window. profiles.load_profiles parses + validates the TOML (built-in default first);
render() substitutes {key} params (shell-quoted into the command, raw into the cwd); the
router launches via tmux.new_window into the current group (or the default session)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse import profiles
from muse.routers import tmux as tmux_router


@pytest.fixture
def profiles_file(tmp_path, monkeypatch):
    """Point profiles.toml at a tmp file; return a writer for the test to fill in."""
    path = tmp_path / "profiles.toml"
    monkeypatch.setenv("MUSE_PROFILES_PATH", str(path))

    def write(text: str) -> None:
        path.write_text(text)

    write.path = path  # type: ignore[attr-defined]
    return write


# --- load_profiles ----------------------------------------------------------------


def test_no_file_yields_only_builtin_default(profiles_file):
    got = profiles.load_profiles()
    assert [p.name for p in got] == ["Claude"]
    assert got[0].builtin and got[0].command == "claude"


def test_parses_profiles_default_first_in_file_order(profiles_file):
    profiles_file(
        """
        [[profile]]
        name = "igloo dev"
        cwd = "~/workspace/igloo-dev"
        command = "./dev.sh {issue} && claude"
        params = [{ key = "issue", prompt = "Issue name" }]

        [[profile]]
        name = "web"
        cwd = "~/workspace/web"
        command = "npm run dev & claude"
        """
    )
    got = profiles.load_profiles()
    assert [p.name for p in got] == ["Claude", "igloo dev", "web"]
    igloo = got[1]
    assert igloo.params[0].key == "issue" and igloo.params[0].default == ""
    assert not igloo.builtin


def test_file_profile_named_claude_overrides_builtin(profiles_file):
    profiles_file(
        """
        [[profile]]
        name = "Claude"
        cwd = "~/work"
        command = "claude --resume"
        """
    )
    got = profiles.load_profiles()
    assert [p.name for p in got] == ["Claude"]  # not duplicated
    assert got[0].command == "claude --resume" and not got[0].builtin


@pytest.mark.parametrize(
    "text",
    [
        "this is = not valid toml [[",  # decode error
        'profile = "not an array"',  # profile is not a list of tables
        '[[profile]]\nname = "a"\n[[profile]]\nname = "A"',  # duplicate (case-insensitive)
        '[[profile]]\nname = "a"\nbogus = "x"',  # unknown key (extra="forbid")
        "[[profile]]\ncwd = \"~\"",  # missing required name
    ],
)
def test_bad_config_raises_profile_error(profiles_file, text):
    profiles_file(text)
    with pytest.raises(profiles.ProfileError):
        profiles.load_profiles()


# --- render -----------------------------------------------------------------------


def test_render_quotes_command_params_and_leaves_cwd_raw():
    p = profiles.Profile(
        name="p",
        cwd="~/w/{repo}",
        command="./dev.sh {issue} && claude",
        params=[
            profiles.ProfileParam(key="repo", prompt="Repo"),
            profiles.ProfileParam(key="issue", prompt="Issue"),
        ],
    )
    cwd, command = profiles.render(p, {"repo": "my proj", "issue": "bug 42; rm -rf"})
    # cwd substituted raw (tmux -c is a literal arg, not shell-interpreted) + ~ expanded.
    assert cwd.endswith("/w/my proj") and not cwd.startswith("~")
    # command value is shell-quoted into a single safe token.
    assert command == "./dev.sh 'bug 42; rm -rf' && claude"


def test_render_missing_value_falls_back_to_default():
    p = profiles.Profile(
        name="p",
        command="echo {greet}",
        params=[profiles.ProfileParam(key="greet", prompt="Greeting", default="hi")],
    )
    _, command = profiles.render(p, {})
    assert command == "echo hi"


# --- window_label -----------------------------------------------------------------


def test_window_label_defaults_to_first_param_value():
    p = profiles.Profile(
        name="igloo dev",
        params=[profiles.ProfileParam(key="issue", prompt="Issue")],
    )
    assert profiles.window_label(p, {"issue": "PROJ-12"}) == "PROJ-12"


def test_window_label_falls_back_to_profile_name_without_params():
    assert profiles.window_label(profiles.Profile(name="web"), {}) == "web"


def test_window_label_honors_explicit_template_and_truncates():
    p = profiles.Profile(
        name="p",
        window_name="igloo/{issue}",
        params=[profiles.ProfileParam(key="issue", prompt="Issue")],
    )
    assert profiles.window_label(p, {"issue": "abc"}) == "igloo/abc"
    assert len(profiles.window_label(p, {"issue": "x" * 100})) == 40


# --- match_cleanup / run_cleanup (session removal) --------------------------------


def test_match_cleanup_matches_by_cwd_glob_and_substitutes(profiles_file, tmp_path):
    home = str(tmp_path)
    profiles_file(
        f"""
        [[profile]]
        name = "igloo"
        cwd = "{home}/igloo-dev"
        command = "claude"
        match_cwd = "{home}/igloo-dev/projects/*"
        cleanup = "./do_worktree.sh remove {{basename}}"
        """
    )
    m = profiles.match_cleanup(f"{home}/igloo-dev/projects/my issue", "my issue")
    assert m is not None
    assert m["profile"] == "igloo"
    assert m["run_cwd"] == f"{home}/igloo-dev"
    # {basename} is the cwd basename, shell-quoted (has a space).
    assert m["command"] == "./do_worktree.sh remove 'my issue'"


def test_match_cleanup_none_when_no_glob_or_no_cleanup(profiles_file, tmp_path):
    home = str(tmp_path)
    profiles_file(
        f"""
        [[profile]]
        name = "no-clean"
        cwd = "{home}/x"
        match_cwd = "{home}/x/*"

        [[profile]]
        name = "has-clean"
        cwd = "{home}/y"
        match_cwd = "{home}/y/*"
        cleanup = "echo bye"
        """
    )
    assert profiles.match_cleanup(f"{home}/x/w", "w") is None  # matches but no cleanup
    assert profiles.match_cleanup("/somewhere/else", "w") is None  # no glob match
    assert profiles.match_cleanup(f"{home}/y/w", "w")["command"] == "echo bye"


def test_run_cleanup_executes_in_run_cwd(tmp_path):
    ok, out = profiles.run_cleanup("pwd", str(tmp_path))
    assert ok and out.strip().endswith(str(tmp_path).split("/")[-1])
    bad_ok, _ = profiles.run_cleanup("exit 3", str(tmp_path))
    assert bad_ok is False


# --- router endpoints -------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.entries = []

    def log(self, sid, action, detail=""):
        self.entries.append((sid, action, detail))


@pytest.fixture
def client(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(tmux_router.router)
    app.state.autopilot = type("AP", (), {"store": FakeStore()})()

    calls: list[tuple] = []
    monkeypatch.setattr(tmux_router.tmux, "available", lambda: True)
    monkeypatch.setattr(tmux_router, "_default_session", lambda: "main")
    monkeypatch.setattr(
        tmux_router.tmux,
        "new_window",
        lambda cwd, command, session=None, name=None: calls.append((cwd, command, session, name))
        or (True, "%9"),
    )
    c = TestClient(app)
    c.calls = calls
    return c


def test_list_profiles_includes_builtin(client, profiles_file):
    r = client.get("/api/tmux/profiles")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()] == ["Claude"]


def test_list_profiles_surfaces_broken_config(client, profiles_file):
    profiles_file("nope [[")
    r = client.get("/api/tmux/profiles")
    assert r.status_code == 400 and "profiles" in r.json()["detail"].lower()


def test_launch_default_profile_uses_default_session(client, profiles_file):
    r = client.post("/api/tmux/profiles/Claude/launch", json={"values": {}})
    assert r.status_code == 200 and r.json() == {"ok": True, "pane_id": "%9"}
    home = __import__("os").path.expanduser("~")
    # No params → the window is labelled with the profile name.
    assert client.calls == [(home, "claude", "main", "Claude")]


def test_launch_renders_params_and_targets_group(client, profiles_file, tmp_path):
    scratch = tmp_path / "proj"
    scratch.mkdir()
    profiles_file(
        f"""
        [[profile]]
        name = "p"
        cwd = "{scratch}"
        command = "echo {{issue}}; bash"
        params = [{{ key = "issue", prompt = "Issue" }}]
        """
    )
    r = client.post(
        "/api/tmux/profiles/p/launch",
        json={"values": {"issue": "abc 1"}, "session": "work"},
    )
    assert r.status_code == 200
    # First param value becomes the window label; command param is shell-quoted.
    assert client.calls == [(str(scratch), "echo 'abc 1'; bash", "work", "abc 1")]


def test_launch_unknown_profile_is_404(client, profiles_file):
    r = client.post("/api/tmux/profiles/ghost/launch", json={"values": {}})
    assert r.status_code == 404 and client.calls == []


def test_launch_rejects_missing_cwd(client, profiles_file, tmp_path):
    profiles_file(
        f'[[profile]]\nname = "p"\ncwd = "{tmp_path / "does-not-exist"}"\ncommand = "claude"'
    )
    r = client.post("/api/tmux/profiles/p/launch", json={"values": {}})
    assert r.status_code == 400 and "not a directory" in r.json()["detail"]
    assert client.calls == []


def test_launch_rejects_bad_session_name(client, profiles_file):
    r = client.post("/api/tmux/profiles/Claude/launch", json={"values": {}, "session": "a:b"})
    assert r.status_code == 400 and client.calls == []
