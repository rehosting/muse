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


# Claude Code renders AskUserQuestion with a header chip, a description indented
# under each option, and a rule above the trailing "Chat about this" row — so the
# numbered rows are NOT adjacent. Captured from a live 2.1.243 pane.
ASK_USER_QUESTION = """\
 ☐ Indentation
Do you prefer tabs or spaces for indentation?
❯ 1. Spaces
     Indent with space characters, which render consistently across every editor.
  2. Tabs
     Indent with tab characters, letting each reader set their own indent width.
  3. Match the project
     Follow whatever the surrounding file or repo config already uses.
  4. Type something.
───────────────────────────────────────────────────────────
  5. Chat about this
Enter to select · ↑/↓ to navigate · Esc to cancel
"""


def test_parse_ask_user_question_with_descriptions_between_rows():
    menu = parse_permission_menu(ASK_USER_QUESTION)
    assert menu is not None  # regression: description lines used to break the block
    assert [o.label for o in menu.options] == [
        "Spaces", "Tabs", "Match the project", "Type something.", "Chat about this",
    ]
    assert menu.current_index == 0
    assert menu.options[0].description.startswith("Indent with space characters")
    # The header chip is not the question, and the rule/footer are not descriptions.
    assert menu.prompt == "Do you prefer tabs or spaces for indentation?"
    assert menu.options[4].description is None


def test_description_capture_ignores_unindented_following_lines():
    text = "Pick one\n❯ 1. Alpha\n     details for alpha\n  2. Beta\nnot a description\n"
    menu = parse_permission_menu(text)
    assert menu.options[0].description == "details for alpha"
    assert menu.options[1].description is None  # left-aligned line isn't indented under it


def test_gapped_prose_numbers_still_rejected():
    # Numbers spread across ordinary output, no ❯ cursor anywhere.
    text = "Plan:\n1. one\n\n   blah\n2. two\n\n   blah\n3. three\n"
    assert parse_permission_menu(text) is None


def test_far_apart_numbers_are_not_one_menu():
    filler = "\n".join(f"line {i}" for i in range(10))
    text = f"❯ 1. Alpha\n{filler}\n  2. Beta\n"
    assert parse_permission_menu(text) is None
