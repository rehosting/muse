"""Enumerate the slash commands available to a Claude Code session, so the phone
composer can offer a searchable "/" menu like the CLI does.

There is no API that lists them, so we reconstruct the set from the same sources
Claude Code reads: a curated list of built-ins, the user's global command files
(~/.claude/commands/**.md), and the project's command files ({cwd}/.claude/
commands/**.md). Everything here is READ-ONLY — we never touch ~/.claude.
"""

from __future__ import annotations

import os
from pathlib import Path

# Built-in Claude Code slash commands. Kept short-and-common on purpose — the
# custom (user/project) commands are the ones worth discovering; the built-ins
# are just here so the menu isn't empty and the familiar names still complete.
_BUILTINS: list[tuple[str, str]] = [
    ("add-dir", "Add a new working directory"),
    ("agents", "Manage custom subagents"),
    ("clear", "Clear conversation history"),
    ("compact", "Summarize and compact the conversation"),
    ("config", "Open the config panel"),
    ("context", "Visualize current context usage"),
    ("cost", "Show token usage and cost"),
    ("doctor", "Diagnose the Claude Code installation"),
    ("help", "List available commands"),
    ("init", "Initialize a CLAUDE.md for this project"),
    ("mcp", "Manage MCP server connections"),
    ("memory", "Edit Claude memory files"),
    ("model", "Select the active model"),
    ("permissions", "View or edit tool permissions"),
    ("pr-comments", "Fetch comments from a GitHub PR"),
    ("resume", "Resume a previous conversation"),
    ("review", "Review a pull request"),
    ("rewind", "Rewind the conversation to an earlier point"),
    ("status", "Show account and system status"),
    ("terminal-setup", "Install terminal key bindings"),
    ("vim", "Toggle vim editing mode"),
]

_MAX_DESC = 100
# A sane ceiling so a pathological commands tree can't stall the poll.
_MAX_FILES = 400


def _description(path: Path) -> str:
    """Best-effort one-liner for a command file: the frontmatter `description:` if
    present, else the first meaningful line of the body."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    # YAML frontmatter: a leading `---` block. Pull `description:` out of it.
    if lines and lines[0].strip() == "---":
        for ln in lines[1:]:
            if ln.strip() == "---":
                break
            key, sep, val = ln.partition(":")
            if sep and key.strip().lower() == "description":
                return val.strip().strip("'\"")[:_MAX_DESC]
    # Otherwise the first non-blank line of the body (skip frontmatter + headings).
    in_fm = bool(lines) and lines[0].strip() == "---"
    for ln in lines:
        s = ln.strip()
        if in_fm:
            if s == "---":
                in_fm = False
            continue
        if s and not s.startswith("---"):
            return s.lstrip("# ").strip()[:_MAX_DESC]
    return ""


def _scan(base: Path, source: str) -> list[dict]:
    """All *.md command files under base/, named by their path relative to base
    with directories joined by ':' (Claude Code's namespacing)."""
    out: list[dict] = []
    if not base.is_dir():
        return out
    count = 0
    for path in sorted(base.rglob("*.md")):
        if not path.is_file():
            continue
        count += 1
        if count > _MAX_FILES:
            break
        rel = path.relative_to(base).with_suffix("")
        name = ":".join(rel.parts)
        if not name:
            continue
        out.append({"name": name, "description": _description(path), "source": source})
    return out


def list_commands(cwd: str | None) -> list[dict]:
    """The slash commands available in a session rooted at `cwd`: built-ins, the
    user's global commands, then the project's. A more-specific definition wins on
    a name collision (project > user > builtin), and the result is sorted by name."""
    by_name: dict[str, dict] = {}
    for name, desc in _BUILTINS:
        by_name[name] = {"name": name, "description": desc, "source": "builtin"}

    home = os.path.expanduser("~")
    if home:
        for c in _scan(Path(home) / ".claude" / "commands", "user"):
            by_name[c["name"]] = c
    if cwd:
        for c in _scan(Path(cwd) / ".claude" / "commands", "project"):
            by_name[c["name"]] = c

    return sorted(by_name.values(), key=lambda c: c["name"])
