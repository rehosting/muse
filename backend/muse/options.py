"""Detect the options a live session is asking the user to choose between.

Two independent sources, surfaced through one shape (`ParsedMenu`):

  1. Permission / selection dialogs that exist ONLY in the terminal — parsed from
     `tmux capture-pane` text by spotting a numbered list (`1.`/`2.`/`3.`) with the
     `❯` highlight marker, adjacent to a prompt line. This is the fragile source, so
     selection acts on it via position-independent digit hotkeys and a re-parse guard.

  2. Structured tool calls (`AskUserQuestion`, `ExitPlanMode`) that muse already parses
     into a `Thread`. These carry their options as data, so detection is exact: the
     latest such tool_use with no paired tool_result is still pending.

This module is pure (no IO): callers pass in captured pane text / an already-loaded
`Thread`, and the routers own the tmux + service plumbing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from .models import Thread

OptionSource = Literal["permission", "tool_question"]

# A numbered option row, optionally highlighted by Claude Code's `❯` marker.
_OPTION_RE = re.compile(r"^\s*(?P<marker>[❯>›])?\s*(?P<num>\d+)[.)]\s+(?P<label>.*\S)\s*$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Box-drawing glyphs tmux may leave at the start/end of a captured line.
_BOX_LEAD_RE = re.compile(r"^[\s│|╭╰─┌└┃]+")
_BOX_TRAIL_RE = re.compile(r"[\s│|╮╯─┐┘┃]+$")
_HIGHLIGHT = "❯›>"

_TOOL_QUESTION_NAMES = {"AskUserQuestion", "ExitPlanMode"}

# Claude Code's permission mode, shown on the bottom status line and cycled with
# Shift+Tab. We detect it from the captured pane so the UI can display + change it.
PermissionMode = Literal["default", "acceptEdits", "plan", "bypass"]
_MODE_PLAN_RE = re.compile(r"plan mode on", re.I)
_MODE_BYPASS_RE = re.compile(r"bypass(?:ing)? permissions", re.I)
_MODE_ACCEPT_RE = re.compile(r"(?:accept edits|auto mode) on", re.I)
# Markers that prove this pane is a Claude Code UI (so non-Claude shells show no chip).
_MODE_CLAUDE_RE = re.compile(r"shift\+tab to cycle|\? for shortcuts", re.I)


def parse_mode(pane_text: str) -> Optional[str]:
    """Detect Claude Code's permission mode from a captured pane.

    Returns one of default/acceptEdits/plan/bypass, or None if the pane isn't a
    Claude Code UI at all (a plain shell shouldn't get a mode chip). The mode hint
    lives on the bottom status line, so we only scan the tail."""
    tail = "\n".join(_ANSI_RE.sub("", pane_text).splitlines()[-8:])
    if _MODE_PLAN_RE.search(tail):
        return "plan"
    if _MODE_BYPASS_RE.search(tail):
        return "bypass"
    if _MODE_ACCEPT_RE.search(tail):
        return "acceptEdits"
    if _MODE_CLAUDE_RE.search(tail):
        return "default"
    return None


@dataclass
class MenuOption:
    id: str  # "1".."9" for menus; option label-hash for tool questions; "other"
    label: str
    description: Optional[str] = None
    kind: Literal["menu", "free_text"] = "menu"


@dataclass
class ParsedMenu:
    source: OptionSource
    prompt: str
    options: list[MenuOption] = field(default_factory=list)
    current_index: Optional[int] = None  # highlighted row, 0-based among options
    remaining_questions: int = 0  # AskUserQuestion with >1 question still pending


def fingerprint(prompt: str, options: list[MenuOption]) -> str:
    """Stable hash of what the user is being shown. The client echoes it back on
    select so the server can refuse (409) if the buffer changed underneath."""
    h = hashlib.sha256()
    h.update(prompt.strip().encode("utf-8", "replace"))
    for opt in options:
        h.update(b"\x00")
        h.update(opt.label.strip().encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _clean(line: str) -> str:
    line = _ANSI_RE.sub("", line)
    return _BOX_TRAIL_RE.sub("", _BOX_LEAD_RE.sub("", line))


# Claude Code renders each option's description on the line(s) beneath it, so a
# menu's numbered rows are no longer adjacent. How far apart two consecutive
# numbers may sit and still count as one menu (descriptions wrap; a horizontal
# rule can sit between the last two rows).
_MAX_OPTION_GAP = 6
# Leading box glyphs only, NOT spaces: indentation is what separates a wrapped
# description from the next option, so it has to survive cleaning.
_BOX_ONLY_LEAD_RE = re.compile(r"^[│|╭╰┌└┃]+")
# The question's header chip ("☐ Indentation") sits above the question text.
_CHIP_RE = re.compile(r"^[☐☑☒◻◼▣]\s*\S")


def _indent_of(raw_line: str) -> int:
    stripped = _BOX_ONLY_LEAD_RE.sub("", raw_line)
    return len(stripped) - len(stripped.lstrip())


def _menu_rows(rows: list[tuple[int, "re.Match"]]) -> Optional[list[tuple[int, "re.Match"]]]:
    """Walk back from the bottom-most numbered row to `1`, stepping over the
    description lines between options. Returns the rows in display order, or None
    when no 1..N run is present."""
    for end in range(len(rows) - 1, -1, -1):
        total = int(rows[end][1].group("num"))
        if total < 2:
            continue
        chosen = [rows[end]]
        want = total - 1
        k = end - 1
        while k >= 0 and want >= 1:
            line_no, m = rows[k]
            num = int(m.group("num"))
            if num == want and chosen[-1][0] - line_no <= _MAX_OPTION_GAP:
                chosen.append(rows[k])
                want -= 1
            elif num < want or num > total:
                break  # numbering jumped — these rows aren't one menu
            k -= 1
        if want == 0:
            return list(reversed(chosen))
    return None


def _description(raw: list[str], lines: list[str], line_no: int, stop: int) -> Optional[str]:
    """The description Claude Code indents underneath an option row (if any)."""
    base = _indent_of(raw[line_no])
    parts = [
        lines[i].strip()
        for i in range(line_no + 1, stop)
        if lines[i].strip() and _indent_of(raw[i]) > base
    ]
    return " ".join(parts) or None


def _prompt_above(lines: list[str], first_line: int) -> str:
    """The question text sitting above the first option, skipping the header chip."""
    prompt_lines: list[str] = []
    for ln in reversed(lines[:first_line]):
        text = ln.strip()
        if not text:
            if prompt_lines:
                break
            continue
        if _OPTION_RE.match(ln) or _CHIP_RE.match(text):
            break
        prompt_lines.append(text)
        if len(prompt_lines) >= 3:
            break
    return " ".join(reversed(prompt_lines)).strip()


def parse_permission_menu(pane_text: str) -> Optional[ParsedMenu]:
    """Find a numbered selection dialog in captured pane text, or None.

    Gate (to reject ordinary numbered prose in assistant output): ≥2 options
    numbered 1..N, plus the live `❯` cursor. The rows need NOT be adjacent —
    AskUserQuestion prints a description under each option and a rule above the
    trailing "Chat about this" row — so we walk the numbers back from the bottom
    rather than demanding one contiguous block.
    """
    if not pane_text:
        return None
    raw = [_ANSI_RE.sub("", ln) for ln in pane_text.splitlines()]
    lines = [_clean(ln) for ln in raw]

    rows: list[tuple[int, re.Match]] = []
    for i, ln in enumerate(lines):
        m = _OPTION_RE.match(ln)
        if m:
            rows.append((i, m))
    best = _menu_rows(rows) if rows else None
    if not best:
        return None

    options: list[MenuOption] = []
    current_index: Optional[int] = None
    for idx, (line_no, m) in enumerate(best):
        if m.group("marker") and m.group("marker") in _HIGHLIGHT:
            current_index = idx
        stop = (
            best[idx + 1][0]
            if idx + 1 < len(best)
            else min(len(lines), line_no + _MAX_OPTION_GAP)
        )
        options.append(
            MenuOption(
                id=m.group("num"),
                label=m.group("label"),
                description=_description(raw, lines, line_no, stop),
                kind="menu",
            )
        )

    # Explicit menus only: require the live `❯` selection cursor. A numbered list
    # in ordinary output (a plan, a list of steps) has no cursor and must NOT be
    # surfaced as tappable options.
    if current_index is None:
        return None

    return ParsedMenu(
        source="permission",
        prompt=_prompt_above(lines, best[0][0]),
        options=options,
        current_index=current_index,
    )


def find_pending_tool_question(thread: Optional[Thread]) -> Optional[ParsedMenu]:
    """The latest AskUserQuestion/ExitPlanMode tool_use with no result yet, or None.

    `result is None` is the precise pending signal: the transcript only records the
    user's tool_result once they answer. Only Claude provider emits these tools.
    """
    if thread is None or thread.provider != "claude":
        return None
    for item in reversed(thread.items):
        for block in item.blocks:
            tu = block.tool_use
            if not tu or tu.name not in _TOOL_QUESTION_NAMES or tu.result is not None:
                continue
            if tu.name == "ExitPlanMode":
                return _exit_plan_menu(tu.input)
            return _ask_user_question_menu(tu.input)
    return None


def _ask_user_question_menu(tool_input: dict) -> ParsedMenu:
    questions = tool_input.get("questions") or []
    if not questions:
        return ParsedMenu(source="tool_question", prompt="(question)", options=[])
    q = questions[0]
    prompt = (q.get("question") or q.get("header") or "Select an option").strip()
    options: list[MenuOption] = []
    for i, opt in enumerate(q.get("options") or []):
        label = (opt.get("label") or "").strip()
        if not label:
            continue
        options.append(
            MenuOption(
                id=str(i + 1),
                label=label,
                description=(opt.get("description") or "").strip() or None,
                kind="menu",
            )
        )
    # Always offer a free-text escape hatch (routes through the text path).
    options.append(MenuOption(id="other", label="Other (type a reply)", kind="free_text"))
    return ParsedMenu(
        source="tool_question",
        prompt=prompt,
        options=options,
        remaining_questions=max(0, len(questions) - 1),
    )


def _exit_plan_menu(tool_input: dict) -> ParsedMenu:
    plan = (tool_input.get("plan") or "").strip()
    first = plan.splitlines()[0].strip() if plan else ""
    prompt = "Ready to code? " + (f"Plan: {first}" if first else "(plan presented)")
    return ParsedMenu(
        source="tool_question",
        prompt=prompt,
        options=[
            MenuOption(id="1", label="Yes, proceed", kind="menu"),
            MenuOption(id="2", label="No, keep planning", kind="menu"),
        ],
    )
