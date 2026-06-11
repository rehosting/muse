"""Tests for per-file change aggregation."""

from datetime import datetime, timezone

from muse import changes
from muse.models import (
    ContentBlock,
    Thread,
    ThreadItem,
    ToolResult,
    ToolUse,
)


def _ts(sec: int) -> datetime:
    return datetime(2026, 6, 7, 0, 0, sec, tzinfo=timezone.utc)


def _tool_item(uuid, ts, tool):
    return ThreadItem(
        uuid=uuid,
        role="assistant",
        type="assistant",
        timestamp=ts,
        blocks=[ContentBlock(kind="tool_use", tool_use=tool)],
    )


def _thread(items):
    return Thread(session_id="s", title="t", items=items)


def test_aggregates_per_file_counts_and_ops():
    items = [
        _tool_item("u1", _ts(0), ToolUse(id="t1", name="Read", input={"file_path": "a.py"})),
        _tool_item(
            "u2", _ts(1),
            ToolUse(id="t2", name="Edit", input={"file_path": "a.py", "old_string": "x", "new_string": "y"}),
        ),
        _tool_item(
            "u3", _ts(2),
            ToolUse(
                id="t3", name="MultiEdit",
                input={"file_path": "a.py", "edits": [{}, {}, {}]},
            ),
        ),
        _tool_item(
            "u4", _ts(3),
            ToolUse(id="t4", name="Write", input={"file_path": "b.py", "content": "hi"}),
        ),
    ]
    files = changes.build_file_changes(_thread(items))

    by_path = {f.path: f for f in files}
    assert set(by_path) == {"a.py", "b.py"}

    a = by_path["a.py"]
    assert a.read_count == 1
    assert a.edit_count == 4  # 1 (Edit) + 3 (MultiEdit hunks)
    assert a.write_count == 0
    assert len(a.ops) == 3
    assert a.first_ts == _ts(0) and a.last_ts == _ts(2)
    assert [o.kind for o in a.ops] == ["read", "edit", "edit"]
    assert a.ops[2].edit_count == 3

    b = by_path["b.py"]
    assert b.write_count == 1 and b.edit_count == 0

    # Most-touched file (a.py, 3 ops) sorts before b.py (1 op).
    assert files[0].path == "a.py"


def test_error_flag_propagates():
    tool = ToolUse(
        id="t1", name="Edit", input={"file_path": "a.py"},
        result=ToolResult(tool_use_id="t1", content="boom", is_error=True),
    )
    files = changes.build_file_changes(_thread([_tool_item("u1", _ts(0), tool)]))
    assert files[0].error_count == 1
    assert files[0].ops[0].is_error is True


def test_ignores_non_file_tools():
    items = [
        _tool_item("u1", _ts(0), ToolUse(id="t1", name="Bash", input={"command": "ls"})),
        _tool_item("u2", _ts(1), ToolUse(id="t2", name="Grep", input={"pattern": "x"})),
    ]
    assert changes.build_file_changes(_thread(items)) == []
