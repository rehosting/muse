"""Cross-session file-activity index: incremental sync + queries."""

from datetime import datetime, timezone

import pytest

from muse.file_index import FileIndex
from muse.models import FileChange, FileOp, SessionSummary


def _summary(sid: str, mtime: float, cwd: str = "/proj") -> SessionSummary:
    return SessionSummary(
        session_id=sid, provider="claude", project_dir="p", project_cwd=cwd,
        title=f"session {sid}", mtime=datetime.fromtimestamp(mtime, tz=timezone.utc),
    )


def _change(path: str, kind: str = "edit", tool_id: str = "t1", err: bool = False):
    fc = FileChange(path=path)
    fc.ops.append(FileOp(tool_use_id=tool_id, kind=kind, tool_name="Edit",
                         timestamp=datetime(2026, 6, 10, tzinfo=timezone.utc),
                         is_error=err))
    setattr(fc, f"{'edit' if kind == 'edit' else kind}_count", 1)
    return fc


@pytest.fixture
def idx(tmp_path):
    i = FileIndex(tmp_path / "muse.db")
    yield i
    i.close()


def test_sync_and_search(idx):
    changes = {"s1": [_change("/proj/a.py"), _change("/proj/b.py", "read", "t2")]}
    n = idx.sync([_summary("s1", 100.0)], lambda sid: changes.get(sid))
    assert n == 1
    hits = idx.search_files("a.py")
    assert len(hits) == 1
    assert hits[0]["file_path"] == "/proj/a.py"
    assert hits[0]["edits"] == 1 and hits[0]["session_count"] == 1
    # path substring matches too
    assert len(idx.search_files("proj")) == 2


def test_sync_skips_unchanged(idx):
    calls = {"n": 0}

    def fn(sid):
        calls["n"] += 1
        return [_change("/proj/a.py")]

    idx.sync([_summary("s1", 100.0)], fn)
    idx.sync([_summary("s1", 100.0)], fn)  # same mtime → skipped
    assert calls["n"] == 1


def test_sync_rate_limits_live_sessions(idx):
    calls = {"n": 0}

    def fn(sid):
        calls["n"] += 1
        return [_change("/proj/a.py")]

    idx.sync([_summary("s1", 100.0)], fn)
    # mtime advanced but within the per-session rate limit → skipped this tick
    idx.sync([_summary("s1", 200.0)], fn)
    assert calls["n"] == 1


def test_prune_vanished_sessions(idx):
    idx.sync([_summary("s1", 100.0)], lambda sid: [_change("/proj/a.py")])
    assert idx.indexed_sessions() == 1
    idx.sync([], lambda sid: [])
    assert idx.indexed_sessions() == 0
    assert idx.search_files("a.py") == []


def test_activity_grouped_by_session(idx):
    idx.sync(
        [_summary("s1", 100.0), _summary("s2", 100.0)],
        lambda sid: [_change("/proj/a.py", tool_id=f"t-{sid}")],
    )
    groups = idx.activity_for("/proj/a.py")
    assert {g["session_id"] for g in groups} == {"s1", "s2"}
    assert all(g["edits"] == 1 for g in groups)
    assert groups[0]["ops"][0]["tool_use_id"].startswith("t-")


def test_edited_files_and_sharing(idx):
    def fn(sid):
        if sid == "s1":
            return [_change("/proj/a.py"), _change("/proj/b.py", "read", "t2")]
        return [_change("/proj/a.py"), _change("/proj/c.py")]

    idx.sync([_summary("s1", 100.0), _summary("s2", 100.0)], fn)
    assert idx.edited_files("s1") == {"/proj/a.py"}  # reads don't count
    sharing = idx.sessions_sharing_files("s1")
    assert len(sharing) == 1
    assert sharing[0]["session_id"] == "s2"
    assert sharing[0]["shared_files"] == ["/proj/a.py"]
