"""Tests for the board snapshot building blocks (activity extraction, rolling
health, 64KB priming) — no tmux, no server."""

import json

from muse.board import _PRIME_BYTES, _activity_from_obj
from muse.patterns import RollingHealth


def _assistant(blocks, ts="2026-06-11T12:00:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"content": blocks}}


def _tool_result(tool_use_id, is_error=False, text="out"):
    return {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id,
                 "is_error": is_error, "content": text}
            ]
        },
    }


def test_activity_assistant_text():
    a = _activity_from_obj(_assistant([{"type": "text", "text": "Fixing the bug\nmore"}]))
    assert a.kind == "assistant_text" and a.text == "Fixing the bug"
    assert a.ts is not None


def test_activity_prefers_tool_call_and_labels_it():
    a = _activity_from_obj(_assistant([
        {"type": "text", "text": "Let me run the tests"},
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "pytest -q"}},
    ]))
    assert a.kind == "tool_call" and a.tool == "Bash" and a.text == "pytest -q"


def test_activity_error_shapes():
    api_err = _activity_from_obj({"isApiErrorMessage": True, "content": "rate limited"})
    assert api_err.kind == "error" and "rate limited" in api_err.text
    tool_err = _activity_from_obj(_tool_result("t1", is_error=True, text="boom"))
    assert tool_err.kind == "error" and "boom" in tool_err.text


def test_activity_user_prompt():
    a = _activity_from_obj({"type": "user", "message": {"content": "do the thing"}})
    assert a.kind == "user" and a.text == "do the thing"


def test_activity_ignores_noise():
    assert _activity_from_obj({"type": "summary"}) is None
    assert _activity_from_obj(_tool_result("t1", is_error=False)) is None


def test_rolling_retry_loop():
    rh = RollingHealth()
    for i in range(4):
        rh.feed(_assistant([{"type": "tool_use", "id": f"t{i}", "name": "Bash",
                             "input": {"command": "make build"}}]))
        rh.feed(_tool_result(f"t{i}", is_error=True, text="error: nope"))
    score, flags = rh.score()
    assert score == "bad"
    assert any("retry loop" in f and "Bash" in f and "×4" in f for f in flags)


def test_rolling_retry_resets_on_success_or_different_tool():
    rh = RollingHealth()
    for i, (tool, err) in enumerate(
        [("Bash", True), ("Bash", True), ("Read", True), ("Read", False)]
    ):
        rh.feed(_assistant([{"type": "tool_use", "id": f"t{i}", "name": tool,
                             "input": {"command": "x"}}]))
        rh.feed(_tool_result(f"t{i}", is_error=err))
    score, flags = rh.score()
    assert not any("retry loop" in f for f in flags)


def test_rolling_error_spiral():
    rh = RollingHealth()
    for i in range(10):
        rh.feed(_assistant([{"type": "tool_use", "id": f"t{i}", "name": f"T{i}",
                             "input": {"command": str(i)}}]))
        rh.feed(_tool_result(f"t{i}", is_error=(i % 2 == 0)))  # 5 of 10
    score, flags = rh.score()
    assert score == "bad" and "error spiral" in flags


def test_rolling_denials():
    rh = RollingHealth()
    for i in range(3):
        rh.feed(_assistant([{"type": "tool_use", "id": f"t{i}", "name": "Bash",
                             "input": {"command": "rm"}}]))
        rh.feed(_tool_result(f"t{i}", is_error=True, text="Permission denied by hook"))
    score, flags = rh.score()
    assert score == "bad"
    assert any("denials" in f for f in flags)


def test_rolling_matches_batch_detector_on_clean_session():
    rh = RollingHealth()
    for i in range(20):
        rh.feed(_assistant([{"type": "tool_use", "id": f"t{i}", "name": "Read",
                             "input": {"file_path": f"/f{i}"}}]))
        rh.feed(_tool_result(f"t{i}", is_error=False))
    assert rh.score() == ("ok", [])


def test_tail_priming_reads_at_most_64kb(tmp_path, monkeypatch):
    """A huge transcript new to the board must not be fully read."""
    from muse.board import BoardTicker
    from muse.config import get_settings

    # Build a fake transcript: ~1MB of filler lines, last line is the activity.
    p = tmp_path / "projects" / "proj" / "big.jsonl"
    p.parent.mkdir(parents=True)
    filler = json.dumps({"type": "summary", "text": "x" * 200})
    lines = [filler] * 5000
    lines.append(json.dumps({"type": "user", "message": {"content": "latest prompt"}}))
    p.write_text("\n".join(lines) + "\n")
    assert p.stat().st_size > _PRIME_BYTES

    monkeypatch.setenv("MUSE_CLAUDE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        ticker = BoardTicker(service=None, broker=None)
        summary = type("S", (), {
            "provider": "claude", "session_id": "big", "project_dir": "proj",
        })()
        activity, rolling = ticker._tail(summary)
        assert activity is not None and activity.text == "latest prompt"
        # Offset advanced to EOF and primed within the tail window.
        assert ticker._offsets["big"] == p.stat().st_size
    finally:
        get_settings.cache_clear()
