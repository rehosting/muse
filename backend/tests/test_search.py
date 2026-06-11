"""Tests for cross-session FTS5 search (provider-driven, append-only sync)."""

import orjson
import pytest

from muse import search
from muse.providers.base import IndexDoc
from muse.search import SearchIndex, _build_match_query


def test_build_match_query_prefix_ands_terms():
    assert _build_match_query("parser bug") == '"parser"* "bug"*'
    assert _build_match_query("   ") is None


def _doc(path, mtime, session_id, rows, append_safe=True, size=None):
    """A fake append-only doc: rows_fn(offset, start_index) returns the rows AFTER
    start_index (so the indexer only sees what's new), plus (new_offset, new_index)."""
    rows = list(rows)
    sz = size if size is not None else len(rows)

    def rows_fn(offset, start_index):
        return rows[start_index:], sz, len(rows)

    return IndexDoc(
        path=path, mtime=mtime, session_id=session_id, project_dir="p",
        rows_fn=rows_fn, size=sz, append_safe=append_safe,
    )


@pytest.fixture
def idx(tmp_path):
    index = SearchIndex(tmp_path / "muse.db")
    if not index.available:
        pytest.skip("SQLite build lacks FTS5")
    yield index
    index.close()


def test_sync_and_search_roundtrip(idx):
    rows = [("u1", "user", "2026-06-07T00:00:00Z", "please refactor the tokenizer module"),
            ("ci3", "assistant", "2026-06-07T00:00:01Z", "done refactoring")]
    added = idx.sync([_doc("/s/a.jsonl", 100.0, "codex:abc", rows)])
    assert added == 2
    assert idx.indexed_sessions() == 1

    hits = idx.search("tokenizer")
    assert len(hits) == 1
    assert hits[0]["session_id"] == "codex:abc"
    assert hits[0]["uuid"] == "u1"
    assert search.MARK_START in hits[0]["snippet"]

    # Same mtime → unchanged → not re-indexed.
    assert idx.sync([_doc("/s/a.jsonl", 100.0, "codex:abc", rows)]) == 0

    # Vanished file is pruned.
    idx.sync([])
    assert idx.indexed_sessions() == 0
    assert idx.search("tokenizer") == []


def test_append_only_adds_just_new_rows(idx):
    r1 = ("u1", "user", None, "alpha tokenizer")
    r2 = ("u2", "assistant", None, "beta parser")
    assert idx.sync([_doc("/s/a.jsonl", 1.0, "codex:x", [r1], size=1)]) == 1
    # File grew by one row; append path adds ONLY the new row (no DELETE/re-tokenize).
    added = idx.sync([_doc("/s/a.jsonl", 2.0, "codex:x", [r1, r2], size=2)])
    assert added == 1
    # Both old and new rows are searchable, and the old row isn't duplicated.
    assert len(idx.search("alpha")) == 1
    assert len(idx.search("beta")) == 1


def test_truncation_triggers_full_reindex(idx):
    r1 = ("u1", "user", None, "alpha")
    r2 = ("u2", "user", None, "beta")
    assert idx.sync([_doc("/s/a.jsonl", 1.0, "codex:x", [r1, r2], size=2)]) == 2
    # Size shrank → the file was rewritten/truncated → full reindex, old rows gone.
    added = idx.sync([_doc("/s/a.jsonl", 2.0, "codex:x", [("u3", "user", None, "gamma")], size=1)])
    assert added == 1
    assert idx.search("alpha") == [] and idx.search("beta") == []
    assert len(idx.search("gamma")) == 1


def test_non_append_safe_full_reindex(idx):
    # opencode-style doc (append_safe=False): a change fully re-indexes the session,
    # so mutated content is reflected (no stale rows linger).
    assert idx.sync([_doc("oc:1", 1.0, "opencode:1", [("a", "user", None, "alpha")],
                          append_safe=False)]) == 1
    idx.sync([_doc("oc:1", 2.0, "opencode:1", [("a", "user", None, "omega")],
                   append_safe=False)])
    assert idx.search("alpha") == []
    assert len(idx.search("omega")) == 1


def _write_jsonl(path, objs):
    path.write_bytes(b"".join(orjson.dumps(o) + b"\n" for o in objs))


def test_claude_line_rows_append_continues(tmp_path):
    from muse.providers.claude_code import _line_rows
    p = tmp_path / "a.jsonl"
    _write_jsonl(p, [{"type": "assistant", "uuid": "u1",
                      "message": {"content": [{"type": "text", "text": "hello alpha"}]}}])
    rows, off1, idx1 = _line_rows(p, 0, 0)
    assert [r[0] for r in rows] == ["u1"] and idx1 == 1
    # Append a line; resume from the saved offset → only the new row, index continues.
    with p.open("ab") as fh:
        fh.write(orjson.dumps({"type": "assistant", "uuid": "u2",
                               "message": {"content": [{"type": "text", "text": "beta"}]}}) + b"\n")
    rows2, off2, idx2 = _line_rows(p, off1, idx1)
    assert [r[0] for r in rows2] == ["u2"] and idx2 == 2 and off2 > off1


def test_codex_search_rows_index_stays_aligned(tmp_path):
    from muse.providers.codex import _search_rows
    p = tmp_path / "c.jsonl"
    # obj 0 is not a response_item (no row), obj 1 is a message (ci1).
    _write_jsonl(p, [
        {"type": "session_meta", "payload": {"type": "session_meta"}},
        {"type": "response_item", "timestamp": "t",
         "payload": {"type": "message", "role": "user", "content": "alpha"}},
    ])
    rows, off1, idx1 = _search_rows(p, 0, 0)
    assert [r[0] for r in rows] == ["ci1"] and idx1 == 2
    # Append a 3rd object (ordinal 2) → its id must be ci2, continuing past the boundary.
    with p.open("ab") as fh:
        fh.write(orjson.dumps({"type": "response_item", "timestamp": "t",
                               "payload": {"type": "message", "role": "assistant",
                                           "content": "beta"}}) + b"\n")
    rows2, off2, idx2 = _search_rows(p, off1, idx1)
    assert [r[0] for r in rows2] == ["ci2"] and idx2 == 3
