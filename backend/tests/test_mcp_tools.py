"""Tests for the muse MCP tool layer (read + markup), exercised through FastMCP."""

from datetime import datetime, timezone

import anyio
import pytest

from muse.ai.digest import DigestResult
from muse.investigations import InvestigationStore
from muse.mcp import server as mcp_server
from muse.models import SessionSummary


class FakeService:
    """Backs investigation tools with a real store; stubs the read tools."""

    def __init__(self, store: InvestigationStore):
        self._store = store

    # investigation passthroughs
    def create_investigation(self, *a, **k):
        return self._store.create_investigation(*a, **k)

    def update_investigation(self, *a, **k):
        return self._store.update_investigation(*a, **k)

    def add_reference(self, *a, **k):
        return self._store.add_reference(*a, **k)

    def list_investigations(self):
        return self._store.list_investigations()

    def get_investigation(self, i):
        return self._store.get_investigation(i)

    def get_session_references(self, sid):
        return self._store.get_session_references(sid)

    def reference_freshness(self, sid):
        return None  # no event resolution in the fake; skips the FRESHNESS line

    # read stubs
    def list_sessions(self):
        return [
            SessionSummary(
                session_id="sess-1", provider="claude", project_dir="p", title="T",
                mtime=datetime(2026, 1, 1, tzinfo=timezone.utc), message_count=3,
            )
        ]

    def build_session_digest(self, sid, max_tokens):
        return DigestResult(text=f"DIGEST for {sid}") if sid == "sess-1" else None

    def get_session_tokens(self, sid):
        from muse.models import TokenUsage
        if sid != "sess-1":
            return None
        return TokenUsage(session_id=sid, provider="claude", input_tokens=100,
                          output_tokens=50, total_tokens=150, total_with_cache_read=1150,
                          main_tokens=120, subagent_tokens=30, subagent_count=2)


def _text(result):
    content = result[0] if isinstance(result, tuple) else result
    return "\n".join(getattr(c, "text", "") for c in content)


@pytest.fixture
def mcp(tmp_path):
    store = InvestigationStore(tmp_path / "muse.db")
    mcp_server.set_service(FakeService(store))
    server = mcp_server.build_mcp()
    yield server
    store.close()
    mcp_server.set_service(None)


def test_tool_registry(mcp):
    tools = anyio.run(mcp.list_tools)
    names = {t.name for t in tools}
    assert {"list_sessions", "search_sessions", "get_session", "create_investigation",
            "add_reference", "get_session_references", "list_investigations",
            "get_session_tokens", "get_usage_at_anchor", "get_usage_timeline",
            "list_subagents", "get_session_outline", "get_step", "get_compactions",
            "get_errors", "get_session_steps"} <= names


def test_get_session_digest(mcp):
    assert "DIGEST for sess-1" in _text(
        anyio.run(mcp.call_tool, "get_session", {"session_id": "sess-1"})
    )
    assert "not found" in _text(
        anyio.run(mcp.call_tool, "get_session", {"session_id": "nope"})
    )


def test_list_sessions_tool(mcp):
    out = _text(anyio.run(mcp.call_tool, "list_sessions", {}))
    assert "sess-1" in out and "claude" in out
    assert "tok" in out  # token count surfaced on each line


def test_get_session_tokens_tool(mcp):
    res = anyio.run(mcp.call_tool, "get_session_tokens", {"session_id": "sess-1"})
    payload = res[1] if isinstance(res, tuple) else {}
    text = _text(res)
    blob = text + str(payload)
    assert "150" in blob and "subagent_tokens" in blob  # breakdown incl. subagents
    assert "not found" in _text(anyio.run(mcp.call_tool, "get_session_tokens", {"session_id": "x"}))


def test_markup_roundtrip_through_tools(mcp):
    async def flow():
        created = _text(await mcp.call_tool("create_investigation", {
            "title": "Why it stalled",
            "body": "Looped on install.",
            "refs": [{"session_id": "sess-1", "anchor_uuid": "ci42", "comment": "stall"}],
        }))
        backlinks = _text(await mcp.call_tool("get_session_references", {"session_id": "sess-1"}))
        listing = _text(await mcp.call_tool("list_investigations", {}))
        return created, backlinks, listing

    created, backlinks, listing = anyio.run(flow)
    assert "inv_" in created and "investigations/" in created  # id + muse url
    # read it back as a backlink (the AI rediscovers its own finding)
    assert "Why it stalled" in backlinks and "ci42" in backlinks
    assert "Why it stalled" in listing and "ai" in listing
