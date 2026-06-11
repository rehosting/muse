"""Usage dedupe: one API response streams as several transcript lines (one per
content block), each repeating the SAME usage under the same message id. Usage
must be counted once per message id — summing every line ~doubles totals."""

import orjson

from muse.usage_cache import _file_events


def _line(msg_id, text_block, usage, uuid):
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": "2026-06-10T12:00:00Z",
        "message": {
            "id": msg_id,
            "model": "claude-opus-4-8",
            "content": [text_block],
            "usage": usage,
        },
    }


def _write(path, objs):
    path.write_bytes(b"".join(orjson.dumps(o) + b"\n" for o in objs))


def test_usage_counted_once_per_message_id(tmp_path):
    p = tmp_path / "s.jsonl"
    usage = {"input_tokens": 100, "output_tokens": 50,
             "cache_creation_input_tokens": 10, "cache_read_input_tokens": 1000}
    _write(p, [
        _line("msg_1", {"type": "text", "text": "thinking about it"}, usage, "u1"),
        _line("msg_1", {"type": "tool_use", "name": "Bash", "input": {}}, usage, "u2"),
        _line("msg_2", {"type": "text", "text": "done"},
              {"input_tokens": 5, "output_tokens": 7}, "u3"),
    ])
    events = _file_events(p, "s", "proj", False, "")
    assert sum(e.input for e in events) == 105  # not 205
    assert sum(e.output for e in events) == 57
    assert sum(e.cr for e in events) == 1000
    # the duplicate line still surfaces its tool call (for tool counting)
    assert any("Bash" in e.tools for e in events)


def test_dedupe_survives_incremental_append(tmp_path):
    p = tmp_path / "s.jsonl"
    usage = {"input_tokens": 100, "output_tokens": 50}
    _write(p, [_line("msg_1", {"type": "text", "text": "a"}, usage, "u1")])
    events = _file_events(p, "s", "proj", False, "")
    assert sum(e.input for e in events) == 100
    # Append the SECOND line of the same API message (same id, same usage):
    # the incremental resume must remember msg_1 was already counted.
    with p.open("ab") as fh:
        fh.write(orjson.dumps(
            _line("msg_1", {"type": "tool_use", "name": "Read", "input": {}}, usage, "u2")
        ) + b"\n")
    events = _file_events(p, "s", "proj", False, "")
    assert sum(e.input for e in events) == 100  # unchanged
