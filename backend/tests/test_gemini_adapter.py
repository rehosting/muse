"""Tests for the Gemini provider adapter."""

import json
from types import SimpleNamespace

import pytest

from muse.providers import gemini

UUID = "99d8a8e0-c043-484b-9f59-bd1f5cb37bbb"


def _write_session(gdir):
    chats = gdir / "tmp" / "proj" / "chats"
    chats.mkdir(parents=True)
    lines = [
        {"sessionId": UUID, "projectHash": "h", "startTime": "2026-05-16T02:31:00.000Z",
         "lastUpdated": "2026-05-16T02:35:00.000Z", "kind": "main"},
        {"id": "u0", "timestamp": "2026-05-16T02:31:01.000Z", "type": "user",
         "content": [{"text": "/model"}]},
        {"id": "u1", "timestamp": "2026-05-16T02:31:02.000Z", "type": "user",
         "content": [{"text": "refactor the parser"}]},
        {"id": "g1", "timestamp": "2026-05-16T02:31:05.000Z", "type": "gemini",
         "content": "on it", "thoughts": [{"subject": "plan", "description": "do x"}],
         "model": "gemini-3.1-pro",
         "toolCalls": [{"id": "write_file_1778000000000_0", "name": "write_file",
                        "args": {"file_path": "src/foo.py", "content": "x"}}]},
        {"$set": {"lastUpdated": "2026-05-16T02:35:00.000Z"}},
        {"id": "i1", "timestamp": "2026-05-16T02:31:06.000Z", "type": "info",
         "content": "context updated"},
    ]
    f = chats / f"session-2026-05-16T02-31-{UUID[:8]}.jsonl"
    f.write_text("".join(json.dumps(x) + "\n" for x in lines))
    # persisted tool output for the write_file call
    outdir = gdir / "tmp" / "proj" / "tool-outputs" / f"session-{UUID}"
    outdir.mkdir(parents=True)
    (outdir / "write_file_write_file_1778000000000_0_abc.txt").write_text("wrote file ok")
    return f


@pytest.fixture
def gem_env(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini, "get_settings", lambda: SimpleNamespace(gemini_dir=tmp_path))
    gemini._summary_cache.clear()
    gemini._cwd_map = None
    _write_session(tmp_path)
    return tmp_path


def test_discovery_skips_header_and_slash_title(gem_env):
    p = gemini.GeminiProvider()
    ss = p.iter_sessions()
    assert len(ss) == 1
    s = ss[0]
    assert s.session_id == f"gemini:{UUID}" and s.provider == "gemini"
    assert s.title == "refactor the parser"  # skipped "/model"
    assert s.model == "gemini-3.1-pro"


def test_thread_blocks_and_result_pairing(gem_env):
    p = gemini.GeminiProvider()
    t = p.load_thread(f"gemini:{UUID}")
    assert t is not None and t.model == "gemini-3.1-pro"
    # one gemini line → assistant item with thinking + text + tool_use blocks
    asst = next(it for it in t.items if it.role == "assistant")
    kinds = [b.kind for b in asst.blocks]
    assert kinds == ["thinking", "text", "tool_use"]
    tu = asst.blocks[2].tool_use
    assert tu.name == "write_file" and tu.input["file_path"] == "src/foo.py"
    # result paired from tool-outputs file by the call's ts_idx tail
    assert tu.result is not None and tu.result.content == "wrote file ok"
    assert any(it.role == "system" for it in t.items)  # info line


def test_events_cover_kinds(gem_env):
    p = gemini.GeminiProvider()
    ev = p.build_events(f"gemini:{UUID}")
    kinds = {e.kind for e in ev}
    assert {"user", "thinking", "assistant_text", "tool_call", "system"} <= kinds


def test_file_changes(gem_env):
    p = gemini.GeminiProvider()
    fcs = p.build_file_changes(f"gemini:{UUID}")
    assert [f.path for f in fcs] == ["src/foo.py"]
    assert fcs[0].write_count == 1
