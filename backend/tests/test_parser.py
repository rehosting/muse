"""Unit tests for the tolerant JSONL parser and persisted-output detection."""

from muse.parser import extract_tool_results, parse_line, parse_usage
from muse.persisted import detect_persisted


def test_assistant_with_text_and_tool_use():
    obj = {
        "type": "assistant",
        "uuid": "a1",
        "parentUuid": "u0",
        "timestamp": "2026-06-06T03:32:16.821Z",
        "message": {
            "model": "claude-opus-4-8",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }
    item = parse_line(obj)
    assert item is not None
    assert item.role == "assistant"
    assert item.model == "claude-opus-4-8"
    assert [b.kind for b in item.blocks] == ["text", "tool_use"]
    assert item.blocks[1].tool_use.name == "Bash"
    assert item.usage.input_tokens == 10


def test_user_string_content():
    obj = {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "do a thing"}}
    item = parse_line(obj)
    assert item.role == "user"
    assert item.text == "do a thing"


def test_tool_result_string_and_list():
    list_obj = {
        "type": "user",
        "uuid": "u2",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": [{"type": "text", "text": "out"}]},
            ]
        },
    }
    results = extract_tool_results(list_obj)
    assert len(results) == 1
    assert results[0].tool_use_id == "toolu_1"
    assert results[0].content == "out"


def test_unknown_line_type_is_skipped():
    assert parse_line({"type": "queue-operation", "uuid": "x"}) is None
    assert parse_line({"type": "ai-title", "aiTitle": "hi"}) is None
    assert parse_line({"type": "totally-new-type-2027"}) is None


def test_persisted_output_detection():
    raw = (
        "<persisted-output>\n"
        "Output too large (180.4KB). Full output saved to: "
        "/home/x/.claude/projects/p/s/tool-results/abc123.txt\n\n"
        "Preview (first 2KB):\n"
        "the preview body"
    )
    p = detect_persisted(raw)
    assert p is not None
    assert p.cache_id == "abc123"
    assert p.preview.startswith("the preview body")


def test_persisted_attaches_to_result():
    obj = {
        "type": "user",
        "uuid": "u3",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "t",
                 "content": "<persisted-output>\nFull output saved to: /a/b/tool-results/zz.txt\n\nPreview (first 2KB):\np"},
            ]
        },
    }
    r = extract_tool_results(obj)[0]
    assert r.truncated is True
    assert r.cache_id == "zz"
    assert r.content is None


def test_parse_usage_none():
    assert parse_usage(None) is None
