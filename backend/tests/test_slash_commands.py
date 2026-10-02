"""Slash-command discovery for the composer '/' menu."""

from __future__ import annotations

from pathlib import Path

from muse import slash_commands


def test_builtins_present_without_cwd():
    cmds = slash_commands.list_commands(None)
    names = {c["name"] for c in cmds}
    assert "compact" in names
    assert "model" in names
    assert all(c["source"] == "builtin" for c in cmds if c["name"] == "compact")


def test_sorted_by_name():
    cmds = slash_commands.list_commands(None)
    names = [c["name"] for c in cmds]
    assert names == sorted(names)


def test_project_commands_discovered(tmp_path: Path):
    cmd_dir = tmp_path / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "deploy.md").write_text(
        "---\ndescription: Ship it to prod\n---\nRun the deploy.\n"
    )
    cmds = slash_commands.list_commands(str(tmp_path))
    deploy = next(c for c in cmds if c["name"] == "deploy")
    assert deploy["source"] == "project"
    assert deploy["description"] == "Ship it to prod"


def test_description_falls_back_to_first_line(tmp_path: Path):
    cmd_dir = tmp_path / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "note.md").write_text("# Take a note\nbody\n")
    cmds = slash_commands.list_commands(str(tmp_path))
    note = next(c for c in cmds if c["name"] == "note")
    assert note["description"] == "Take a note"


def test_subdir_commands_are_namespaced(tmp_path: Path):
    cmd_dir = tmp_path / ".claude" / "commands" / "frontend"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "build.md").write_text("Build the frontend\n")
    cmds = slash_commands.list_commands(str(tmp_path))
    assert any(c["name"] == "frontend:build" for c in cmds)


def test_project_overrides_builtin(tmp_path: Path):
    cmd_dir = tmp_path / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "review.md").write_text("Custom review flow\n")
    cmds = slash_commands.list_commands(str(tmp_path))
    review = [c for c in cmds if c["name"] == "review"]
    assert len(review) == 1
    assert review[0]["source"] == "project"


def test_missing_cwd_dir_is_harmless(tmp_path: Path):
    # cwd exists but has no .claude/commands — just the built-ins.
    cmds = slash_commands.list_commands(str(tmp_path))
    assert all(c["source"] != "project" for c in cmds)
