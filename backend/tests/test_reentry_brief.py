"""Re-entry brief: the deterministic 'where you left off' summary, built only
from data muse already has (events, TodoWrite, file changes, worklog notes)."""

from datetime import datetime, timezone

import pytest

from muse.config import get_settings
from muse.models import SessionEvent, SessionSummary
from muse.services.events import EventBroker
from muse.services.session_service import SessionService

SID = "sess-1"


def _summary(mtime: float) -> SessionSummary:
    return SessionSummary(
        session_id=SID, provider="claude", project_dir="p", project_cwd="/proj",
        title="WAL fix", mtime=datetime.fromtimestamp(mtime, tz=timezone.utc),
        state="stopped",
    )


class _FakeTU:
    def __init__(self, todos):
        self.input = {"todos": todos}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    s = SessionService(EventBroker())

    events = [
        SessionEvent(index=0, kind="user", type="user", label="fix the WAL bloat",
                     detail="fix the WAL bloat", anchor_uuid="u0"),
        SessionEvent(index=1, kind="tool_call", type="assistant", tool_name="TodoWrite",
                     tool_use_id="t1", anchor_uuid="a1"),
        SessionEvent(index=2, kind="user", type="user", label="now add a checkpoint",
                     detail="now add a checkpoint", anchor_uuid="u2"),
        SessionEvent(index=3, kind="assistant_text", type="assistant",
                     label="Adding it now", detail="Adding it now", anchor_uuid="a3"),
        SessionEvent(index=4, kind="tool_result", type="user", is_error=True,
                     label="pytest failed", detail="1 failed", tool_use_id="t9",
                     anchor_uuid="a4"),
    ]
    todos = [
        {"content": "add checkpoint", "status": "completed"},
        {"content": "run tests", "status": "in_progress"},
        {"content": "restart server", "status": "pending"},
    ]
    monkeypatch.setattr(s, "get_events", lambda sid, agent_id=None:
                        events if sid == SID else None)
    monkeypatch.setattr(s, "_tool_index", lambda sid: {"t1": _FakeTU(todos)})
    monkeypatch.setattr(s, "get_file_changes", lambda sid, agent_id=None: [])
    monkeypatch.setattr(s, "list_sessions", lambda: [_summary(1000.0)])
    yield s
    for store in (s.store, s.search_index, s.notify_store, s.investigations, s.worklog):
        store.close()
    get_settings.cache_clear()


def test_brief_contents(svc):
    brief = svc.build_reentry_brief(SID)
    assert brief["last_goal"]["text"] == "now add a checkpoint"
    assert brief["last_goal"]["anchor_uuid"] == "u2"
    assert brief["last_assistant"]["text"] == "Adding it now"
    assert [t["content"] for t in brief["open_todos"]] == ["run tests", "restart server"]
    assert brief["done_todos"] == 1
    # the error at index 4 is after the last user turn (index 2)
    assert len(brief["open_errors"]) == 1
    assert brief["open_errors"][0]["label"] == "pytest failed"
    assert brief["resume_command"] == f"claude --resume {SID}"
    assert brief["state"] == "stopped"


def test_brief_unknown_session(svc):
    assert svc.build_reentry_brief("nope") is None


def test_brief_includes_notes(svc):
    svc.worklog.create_note("next: re-run with ASLR off", session_id=SID, kind="next")
    svc.worklog.create_note("AI summary here", session_id=SID, kind="brief", author="ai")
    brief = svc.build_reentry_brief(SID)
    assert brief["next_notes"][0]["body"] == "next: re-run with ASLR off"
    assert brief["latest_ai_brief"]["body"] == "AI summary here"
    assert brief["note_count"] == 2


def test_brief_memoized_by_mtime(svc, monkeypatch):
    b1 = svc.build_reentry_brief(SID)
    calls = {"n": 0}
    orig = svc.get_events

    def counting(sid, agent_id=None):
        calls["n"] += 1
        return orig(sid, agent_id)

    monkeypatch.setattr(svc, "get_events", counting)
    b2 = svc.build_reentry_brief(SID)  # same mtime → cache hit, no event rebuild
    assert calls["n"] == 0 and b2 is b1
    monkeypatch.setattr(svc, "list_sessions", lambda: [_summary(2000.0)])
    svc.build_reentry_brief(SID)  # mtime advanced → recompute
    assert calls["n"] == 1
