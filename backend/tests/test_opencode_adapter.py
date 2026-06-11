"""Tests for the opencode provider adapter (SQLite-backed)."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from muse.providers import opencode

SID = "ses_testopencode000000000000"


def _build_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT, name TEXT);
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT,
            directory TEXT, title TEXT, version TEXT,
            time_created INTEGER, time_updated INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, data TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO project VALUES (?,?,?)",
        ("proj1", "/work/proj", "proj"),
    )
    conn.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)",
        ("child", "proj1", SID, "c", "/work/proj", "subagent run", "1.0", 100, 110),
    )
    conn.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)",
        (SID, "proj1", None, "s", "/work/proj", "Refactor the tokenizer", "1.14.29", 1000, 5000),
    )

    def msg(mid, t, data):
        conn.execute(
            "INSERT INTO message VALUES (?,?,?,?)", (mid, SID, t, json.dumps(data))
        )

    def part(pid, mid, t, data):
        conn.execute(
            "INSERT INTO part VALUES (?,?,?,?,?)", (pid, mid, SID, t, json.dumps(data))
        )

    msg("m1", 1000, {"role": "user", "time": {"created": 1000}})
    part("p1", "m1", 1000, {"type": "text", "text": "refactor the tokenizer please"})

    msg("m2", 2000, {"role": "assistant", "modelID": "gpt-5.4", "time": {"created": 2000},
                     "tokens": {"input": 11160, "output": 145, "reasoning": 318,
                                "cache": {"read": 9999, "write": 0}}})
    part("p2", "m2", 2000, {"type": "reasoning", "text": "thinking about it",
                            "time": {"start": 2000, "end": 2100}})
    part("p3", "m2", 2010, {"type": "text", "text": "Reading the file now"})
    part("p4", "m2", 2020, {
        "type": "tool", "tool": "read", "callID": "call_1",
        "state": {"status": "completed", "input": {"filePath": "/work/proj/tok.py"},
                  "output": "file contents", "time": {"start": 2020, "end": 2120}},
    })
    part("p5", "m2", 2030, {
        "type": "tool", "tool": "edit", "callID": "call_2",
        "state": {"status": "error", "input": {"filePath": "/work/proj/tok.py"},
                  "error": "patch did not apply", "time": {"start": 2030, "end": 2040}},
    })

    # An assistant turn that hit an API error (no usable parts).
    msg("m3", 3000, {"role": "assistant", "modelID": "gpt-5.4",
                     "error": {"name": "APIError", "data": {"message": "model not supported"}},
                     "time": {"created": 3000}})

    conn.commit()
    conn.close()


@pytest.fixture
def oc_env(tmp_path, monkeypatch):
    _build_db(tmp_path / "opencode.db")
    monkeypatch.setattr(opencode, "get_settings", lambda: SimpleNamespace(opencode_dir=tmp_path))
    opencode._sessions_cache = None
    return tmp_path


def test_discovery_excludes_subagents(oc_env):
    p = opencode.OpenCodeProvider()
    sessions = p.iter_sessions()
    assert len(sessions) == 1  # child (parent_id set) excluded
    s = sessions[0]
    assert s.session_id == f"opencode:{SID}"
    assert s.provider == "opencode"
    assert s.title == "Refactor the tokenizer"
    assert s.title_source == "ai-title"
    assert s.model == "gpt-5.4"
    assert s.project_cwd == "/work/proj"
    assert s.size_bytes > 0
    assert s.total_tokens == 11623  # summed from assistant message tokens.total


def test_thread_parse_inline_tool_results(oc_env):
    p = opencode.OpenCodeProvider()
    t = p.load_thread(f"opencode:{SID}")
    assert t is not None
    assert t.version == "1.14.29" and t.model == "gpt-5.4"
    # user + two assistant turns (m3 surfaces the API error as a text block)
    roles = [it.role for it in t.items]
    assert roles == ["user", "assistant", "assistant"]
    asst = t.items[1]
    kinds = [b.kind for b in asst.blocks]
    assert kinds == ["thinking", "text", "tool_use", "tool_use"]
    # tool results are inline — paired without cross-message matching
    read_tu = asst.blocks[2].tool_use
    assert read_tu.id == "call_1" and read_tu.result.content == "file contents"
    assert read_tu.result.is_error is False
    edit_tu = asst.blocks[3].tool_use
    assert edit_tu.result.is_error is True
    # the API-error turn becomes a visible warning text block
    assert "model not supported" in t.items[2].blocks[0].text


def test_events_expand_parts(oc_env):
    p = opencode.OpenCodeProvider()
    ev = p.build_events(f"opencode:{SID}")
    kinds = {e.kind for e in ev}
    assert {"user", "assistant_text", "thinking", "tool_call", "tool_result", "system"} <= kinds
    res = next(e for e in ev if e.kind == "tool_result" and e.tool_use_id == "call_1")
    assert res.duration_ms == 100 and res.status == "ok"
    err = next(e for e in ev if e.kind == "tool_result" and e.tool_use_id == "call_2")
    assert err.is_error is True
    assert any(e.kind == "system" and e.is_error for e in ev)


def test_file_changes(oc_env):
    p = opencode.OpenCodeProvider()
    fcs = p.build_file_changes(f"opencode:{SID}")
    assert [f.path for f in fcs] == ["/work/proj/tok.py"]
    fc = fcs[0]
    assert fc.read_count == 1 and fc.edit_count == 1 and fc.error_count == 1


def test_search_rows_and_docs(oc_env):
    p = opencode.OpenCodeProvider()
    docs = p.search_docs()
    assert len(docs) == 1
    assert docs[0].session_id == f"opencode:{SID}"
    assert docs[0].append_safe is False  # SQLite-backed → full re-index on change
    rows, _off, _idx = docs[0].rows_fn(0, 0)
    bodies = " ".join(r[3] for r in rows)
    assert "refactor the tokenizer" in bodies
    assert "read" in bodies  # tool call indexed
    # each row's uuid is a real message id so deep-links resolve
    assert all(r[0].startswith("m") for r in rows)
