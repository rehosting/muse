"""Incremental session-navigation tools (outline / step / errors / compactions /
paging) — so big sessions never spill to disk."""

from datetime import datetime, timezone

import pytest

from muse.config import get_settings
from muse.models import (
    ContentBlock,
    SessionEvent,
    Thread,
    ThreadItem,
    ToolResult,
    ToolUse,
)
from muse.services.events import EventBroker
from muse.services.session_service import SessionService

_T = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    s = SessionService(EventBroker())
    yield s
    for store in (s.store, s.search_index, s.notify_store, s.investigations):
        store.close()
    get_settings.cache_clear()


def _events():
    return [
        SessionEvent(index=0, kind="user", type="user", anchor_uuid="u1", timestamp=_T,
                     label="do the thing", detail="do the thing in full"),
        SessionEvent(index=1, kind="tool_call", type="assistant", anchor_uuid="a1",
                     tool_use_id="t1", tool_name="Bash", label="Bash(pytest)"),
        SessionEvent(index=2, kind="tool_result", type="user", anchor_uuid="a1",
                     tool_use_id="t1", is_error=True, status="error", label="failed"),
        SessionEvent(index=3, kind="system", type="compact_boundary", anchor_uuid="c1",
                     label="Context compacted", is_compaction=True, detail="preTokens 100k"),
        SessionEvent(index=4, kind="user", type="user", anchor_uuid="sum1",
                     label="This session is being continued…",
                     detail="FULL SUMMARY " * 500),  # the isCompactSummary message
        SessionEvent(index=5, kind="assistant_text", type="assistant", anchor_uuid="a2",
                     label="ok done", detail="ok done"),
    ]


def _thread():
    tr = ToolResult(tool_use_id="t1", content="ERROR: boom\n" * 50, is_error=True)
    tu = ToolUse(id="t1", name="Bash", input={"command": "pytest"}, result=tr)
    return Thread(session_id="s1", title="t", items=[
        ThreadItem(uuid="a1", role="assistant", type="assistant",
                   blocks=[ContentBlock(kind="tool_use", tool_use=tu)]),
    ])


@pytest.fixture
def loaded(svc, monkeypatch):
    monkeypatch.setattr(svc, "get_events", lambda sid, agent_id=None: _events())
    monkeypatch.setattr(svc, "get_thread", lambda sid: _thread())
    return svc


def test_outline_is_skeleton(loaded):
    o = loaded.get_session_outline("s1")
    kinds = [r["kind"] for r in o["outline"]]
    assert kinds == ["user", "error", "compaction", "user"]  # tool_call/assistant_text omitted
    assert o["step_count"] == 6
    assert o["next_offset"] is None and o["truncated"] is False  # small → one page


def test_outline_paginates_huge_session(svc, monkeypatch):
    # A big spine of user turns must page (each result bounded) instead of spilling.
    big = [
        SessionEvent(index=i, kind="user", type="user", anchor_uuid=f"u{i:05d}",
                     timestamp=_T, label="x" * 120, detail="x" * 120)
        for i in range(4000)
    ]
    monkeypatch.setattr(svc, "get_events", lambda sid, agent_id=None: big)
    seen, offset, pages = 0, 0, 0
    while True:
        o = svc.get_session_outline("s1", offset)
        import json as _json
        assert len(_json.dumps(o)) <= svc._MCP_RESULT_CHARS + 2000  # bounded result
        seen += len(o["outline"])
        pages += 1
        if o["next_offset"] is None:
            break
        assert o["next_offset"] > offset  # always advances
        offset = o["next_offset"]
    assert seen == 4000 and pages > 1  # every row delivered, across multiple pages


def test_get_step_message_full(loaded):
    r = loaded.get_step("s1", "u1")
    assert r["found"] and r["steps"][0]["detail"] == "do the thing in full"


def test_get_step_tool_enriched(loaded):
    r = loaded.get_step("s1", "t1")  # by tool_use_id
    step = r["steps"][0]
    assert step["tool_input"] == {"command": "pytest"}
    assert "ERROR: boom" in step["tool_result"]


def test_get_step_full_compaction_summary(loaded):
    r = loaded.get_step("s1", "sum1")
    assert r["found"] and r["steps"][0]["detail"].startswith("FULL SUMMARY")
    assert len(r["steps"][0]["detail"]) > 4000  # untruncated, unlike the digest


def test_get_errors(loaded):
    e = loaded.get_errors("s1")
    assert e["error_count"] == 1
    assert e["errors"][0]["anchor"] == "a1"
    assert "ERROR: boom" in e["errors"][0]["detail"]  # pulled full from the thread


def test_reference_freshness(loaded):
    # No backlinks → None.
    assert loaded.reference_freshness("s1") is None
    # An investigation referencing step "sum1" (index 4 of 6) → 1 step after.
    loaded.create_investigation("finding", refs=[{"session_id": "s1", "anchor_uuid": "sum1"}])
    fr = loaded.reference_freshness("s1")
    assert fr["session_steps"] == 6
    assert fr["last_referenced_step"] == 4
    assert fr["steps_after"] == 1
    assert fr["referencing_investigations"] == 1


def test_get_compactions_with_summary_anchor(loaded):
    c = loaded.get_compactions("s1")
    assert c["count"] == 1
    comp = c["compactions"][0]
    assert comp["summary_anchor"] == "sum1" and comp["summary_truncated"] is True
    assert comp["summary_preview"].startswith("FULL SUMMARY")


def test_paging_and_filter(loaded):
    page = loaded.get_session_steps("s1", offset=0, limit=2)
    assert page["total"] == 6 and len(page["steps"]) == 2 and page["next_offset"] == 2
    errs = loaded.get_session_steps("s1", kinds=["error", "compaction"])
    assert {s["kind"] for s in errs["steps"]} <= {"tool_result", "compaction"}
    assert errs["total"] == 2  # one error + one compaction
