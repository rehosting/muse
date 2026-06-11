"""Tests for the Codex provider adapter."""

import json
from types import SimpleNamespace

import pytest

from muse.providers import codex

UUID = "019e0000-1111-2222-3333-444455556666"


def _line(typ, payload, ts="2026-06-08T00:00:00.000Z"):
    return {"timestamp": ts, "type": typ, "payload": payload}


def _write_session(codex_dir):
    d = codex_dir / "sessions" / "2026" / "06" / "08"
    d.mkdir(parents=True)
    lines = [
        _line("session_meta", {"id": UUID, "cwd": "/work/proj", "cli_version": "1.2.3",
                               "model_provider": "openai"}),
        _line("event_msg", {"type": "task_started", "model": "gpt-5.5",
                            "model_context_window": 258400}),
        _line("response_item", {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": "<environment_context>\n cwd\n</environment_context>"}]}),
        _line("response_item", {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": "refactor the tokenizer"}]}),
        _line("response_item", {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking about it"}]}),
        _line("response_item", {"type": "function_call", "name": "shell",
                                "arguments": json.dumps({"command": ["ls", "-la"]}), "call_id": "c1"},
              ts="2026-06-08T00:00:01.000Z"),
        _line("response_item", {"type": "function_call_output", "call_id": "c1", "output": "total 0"},
              ts="2026-06-08T00:00:03.000Z"),
        _line("response_item", {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2",
                                "input": "*** Begin Patch\n*** Update File: src/foo.py\n+x\n*** End Patch"}),
        _line("response_item", {"type": "custom_tool_call_output", "call_id": "c2", "output": "ok"}),
        _line("response_item", {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": "done"}]}),
        _line("event_msg", {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 2000, "cached_input_tokens": 1000, "output_tokens": 100,
            "reasoning_output_tokens": 0}}}),
        # last (cumulative) wins; real = (6000-1000) + 500 + 178 = 5678
        _line("event_msg", {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 6000, "cached_input_tokens": 1000, "output_tokens": 500,
            "reasoning_output_tokens": 178}}}),
        _line("compacted", {"message": "summary", "replacement_history": []}),
    ]
    f = d / f"rollout-2026-06-08T00-00-00-{UUID}.jsonl"
    f.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return f


@pytest.fixture
def codex_env(tmp_path, monkeypatch):
    codex.get_settings  # noqa: B018
    monkeypatch.setattr(codex, "get_settings", lambda: SimpleNamespace(codex_dir=tmp_path))
    codex._summary_cache.clear()
    _write_session(tmp_path)
    return tmp_path


def test_discovery_and_metadata(codex_env):
    p = codex.CodexProvider()
    sessions = p.iter_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == f"codex:{UUID}"
    assert s.provider == "codex"
    assert s.title == "refactor the tokenizer"  # skipped <environment_context>
    assert s.model == "gpt-5.5"
    assert s.project_cwd == "/work/proj"
    assert s.total_tokens == 5678  # last cumulative token_count wins


def test_thread_parse_and_tool_pairing(codex_env):
    p = codex.CodexProvider()
    t = p.load_thread(f"codex:{UUID}")
    assert t is not None
    assert t.context_window == 258400 and t.model == "gpt-5.5"
    kinds = [(it.role, it.blocks[0].kind if it.blocks else "text") for it in t.items]
    assert ("assistant", "thinking") in kinds
    assert ("assistant", "tool_use") in kinds
    # both tool calls paired with their outputs by call_id
    paired = [b.tool_use for it in t.items for b in it.blocks if b.tool_use and b.tool_use.result]
    assert {tu.id for tu in paired} == {"c1", "c2"}
    shell = next(tu for tu in paired if tu.id == "c1")
    assert shell.input == {"command": ["ls", "-la"]}  # arguments JSON-parsed
    assert shell.result.content == "total 0"


def test_events_and_compaction(codex_env):
    p = codex.CodexProvider()
    ev = p.build_events(f"codex:{UUID}")
    kinds = {e.kind for e in ev}
    assert {"user", "assistant_text", "thinking", "tool_call", "tool_result"} <= kinds
    assert any(e.is_compaction for e in ev)
    # tool duration computed from call→output timestamps (1s→3s = 2000ms)
    res = next(e for e in ev if e.kind == "tool_result" and e.tool_use_id == "c1")
    assert res.duration_ms == 2000


def test_file_changes_from_apply_patch(codex_env):
    p = codex.CodexProvider()
    fcs = p.build_file_changes(f"codex:{UUID}")
    assert [f.path for f in fcs] == ["src/foo.py"]
    assert fcs[0].edit_count == 1


def test_lineage_counts_compactions(codex_env):
    p = codex.CodexProvider()
    lin = p.build_lineage(f"codex:{UUID}")
    assert lin.segment_count == 2  # one compaction → two segments
