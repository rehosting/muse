"""Tests for the complete session event timeline builder."""

import json
from pathlib import Path

from muse import eventlog
from muse.paths import SessionPaths


def _write(tmp_path: Path, lines: list[dict]) -> Path:
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return f


def _build(tmp_path, lines):
    f = _write(tmp_path, lines)
    paths = SessionPaths(project_dir="-tmp", session_id="s")
    return eventlog.build_events(f, paths)


def test_covers_all_conversation_types(tmp_path):
    lines = [
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "do a thing"}},
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2026-06-07T00:00:00Z",
            "message": {
                "model": "claude-opus-4-8",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "I'll run it"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        },
        {
            "type": "user",
            "uuid": "u2",
            "timestamp": "2026-06-07T00:00:02Z",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
            },
        },
        {"type": "system", "uuid": "sx", "subtype": "turn_duration", "durationMs": 2000},
        {"type": "permission-mode", "uuid": "p1", "permissionMode": "plan"},
        {"type": "ai-title", "uuid": "ti", "aiTitle": "My session"},
        {"type": "pr-link", "uuid": "pr", "prNumber": 7, "prRepository": "o/r"},
    ]
    kinds = [e.kind for e in _build(tmp_path, lines)]
    for expected in ("user", "thinking", "assistant_text", "tool_call", "tool_result", "system"):
        assert expected in kinds, f"missing {expected}: {kinds}"
    assert kinds.count("lifecycle") >= 2  # permission-mode, ai-title, pr-link


def test_tool_duration_and_result_status(tmp_path):
    lines = [
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2026-06-07T00:00:00Z",
            "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        },
        {
            "type": "user",
            "uuid": "u2",
            "timestamp": "2026-06-07T00:00:05Z",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "boom", "is_error": True}
                ]
            },
        },
    ]
    events = _build(tmp_path, lines)
    call = next(e for e in events if e.kind == "tool_call")
    result = next(e for e in events if e.kind == "tool_result")
    assert call.duration_ms == 5000
    assert result.is_error and result.status == "error"


def test_dedups_unchanged_lifecycle(tmp_path):
    lines = [{"type": "mode", "uuid": f"m{i}", "mode": "normal"} for i in range(5)]
    lines.append({"type": "mode", "uuid": "m9", "mode": "plan"})
    events = _build(tmp_path, lines)
    mode_events = [e for e in events if e.type == "mode"]
    assert len(mode_events) == 2  # only the two distinct values
