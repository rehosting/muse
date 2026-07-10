"""Pending-option detection: permission-dialog parsing + structured tool questions."""

from muse.models import ContentBlock, Thread, ThreadItem, ToolResult, ToolUse
from muse.options import (
    find_pending_tool_question,
    fingerprint,
    parse_permission_menu,
)

# A typical Claude Code permission dialog as `tmux capture-pane -p` returns it.
PERMISSION = """\
╭──────────────────────────────────────────────╮
│ Bash command                                   │
│   rm -rf build/                                 │
│                                                 │
│ Do you want to proceed?                         │
│ ❯ 1. Yes                                        │
│   2. Yes, and don't ask again this session      │
│   3. No, and tell Claude what to do differently │
╰──────────────────────────────────────────────╯
"""

# Ordinary numbered prose in assistant output must NOT register as a menu.
PROSE = """\
Here's my plan:
1. Read the config file
2. Update the handler
3. Run the tests
Let me get started.
"""


def test_parse_permission_menu_extracts_options_and_highlight():
    menu = parse_permission_menu(PERMISSION)
    assert menu is not None
    assert menu.source == "permission"
    assert "proceed" in menu.prompt.lower()
    assert [o.label for o in menu.options][0] == "Yes"
    assert len(menu.options) == 3
    assert menu.current_index == 0  # ❯ on the first row


def test_parse_permission_menu_rejects_prose():
    assert parse_permission_menu(PROSE) is None


def test_parse_permission_menu_rejects_non_monotonic():
    text = "Pick one\n  1. apple\n  3. cherry\n"
    assert parse_permission_menu(text) is None


def test_parse_permission_menu_empty():
    assert parse_permission_menu("") is None


def test_fingerprint_stable_and_sensitive():
    m1 = parse_permission_menu(PERMISSION)
    fp1 = fingerprint(m1.prompt, m1.options)
    fp2 = fingerprint(m1.prompt, m1.options)
    assert fp1 == fp2
    m1.options[0].label = "Changed"
    assert fingerprint(m1.prompt, m1.options) != fp1


def _thread(*items: ThreadItem, provider: str = "claude") -> Thread:
    return Thread(session_id="s", provider=provider, title="t", items=list(items))


def _ask_item(answered: bool) -> ThreadItem:
    tu = ToolUse(
        id="tu1",
        name="AskUserQuestion",
        input={
            "questions": [
                {
                    "question": "Which database?",
                    "options": [
                        {"label": "Postgres", "description": "relational"},
                        {"label": "SQLite", "description": "embedded"},
                    ],
                }
            ]
        },
        result=ToolResult(tool_use_id="tu1", content="ok") if answered else None,
    )
    return ThreadItem(
        uuid="u1", role="assistant", type="assistant",
        blocks=[ContentBlock(kind="tool_use", tool_use=tu)],
    )


def test_pending_ask_user_question_with_other_option():
    menu = find_pending_tool_question(_thread(_ask_item(answered=False)))
    assert menu is not None
    assert menu.source == "tool_question"
    assert menu.prompt == "Which database?"
    labels = [o.label for o in menu.options]
    assert "Postgres" in labels and "SQLite" in labels
    assert menu.options[-1].kind == "free_text"  # synthetic "Other"


def test_answered_question_is_not_pending():
    assert find_pending_tool_question(_thread(_ask_item(answered=True))) is None


def test_non_claude_provider_ignored():
    assert find_pending_tool_question(
        _thread(_ask_item(answered=False), provider="codex")
    ) is None


def test_exit_plan_mode_detected():
    tu = ToolUse(id="p1", name="ExitPlanMode", input={"plan": "Step one\nStep two"})
    item = ThreadItem(
        uuid="u1", role="assistant", type="assistant",
        blocks=[ContentBlock(kind="tool_use", tool_use=tu)],
    )
    menu = find_pending_tool_question(_thread(item))
    assert menu is not None
    assert len(menu.options) == 2
    assert menu.options[0].label == "Yes, proceed"
    # The full plan body rides along so it's reviewable at the point of answering.
    assert menu.detail == "Step one\nStep two"


def test_exit_plan_fingerprint_tracks_plan_body():
    """Two plans with the same one-line prompt but different bodies must not share a
    fingerprint — otherwise a stale selection could act on a re-issued plan."""
    def menu_for(plan: str):
        tu = ToolUse(id="p1", name="ExitPlanMode", input={"plan": plan})
        item = ThreadItem(
            uuid="u1", role="assistant", type="assistant",
            blocks=[ContentBlock(kind="tool_use", tool_use=tu)],
        )
        return find_pending_tool_question(_thread(item))

    a = menu_for("Do X\nthen Y")
    b = menu_for("Do X\nthen Z")
    assert a.prompt == b.prompt  # same one-liner
    assert fingerprint(a.prompt, a.options, a.detail) != fingerprint(b.prompt, b.options, b.detail)
