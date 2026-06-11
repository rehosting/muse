"""Per-session token usage, including the Claude subagent rollup."""

from datetime import datetime, timedelta, timezone

import pytest

from muse.config import get_settings
from muse.models import SessionEvent, SessionSummary, SubagentRef
from muse.services import session_service as ss_mod
from muse.services.events import EventBroker
from muse.services.session_service import SessionService
from muse.usage_cache import Event, Scan

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _at(mins):
    return _T0 + timedelta(minutes=mins)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    s = SessionService(EventBroker())
    yield s
    for store in (s.store, s.search_index, s.notify_store, s.investigations):
        store.close()
    get_settings.cache_clear()


def _ev(sid, inp, out, cc, cr, sub):
    return Event(sid=sid, project_dir="p", ts=None, input=inp, output=out, cc=cc, cr=cr,
                 model="m", is_subagent=sub, agent_type="x" if sub else "")


def _summary(session_id, provider="claude", **kw):
    return SessionSummary(session_id=session_id, provider=provider, project_dir="p",
                          title="t", mtime=datetime(2026, 1, 1, tzinfo=timezone.utc), **kw)


def test_claude_tokens_include_subagents_with_split(svc, monkeypatch):
    scan = Scan(
        events=[
            _ev("s1", 100, 50, 10, 1000, sub=False),
            _ev("s1", 20, 5, 0, 200, sub=True),   # subagent
            _ev("other", 999, 999, 999, 999, sub=False),  # different session, ignored
        ],
        sessions=2, sessions_by_project={},
    )
    monkeypatch.setattr(ss_mod.usage_cache, "scan_all", lambda: scan)
    monkeypatch.setattr(svc, "list_sessions", lambda: [_summary("s1", subagent_count=1)])

    u = svc.get_session_tokens("s1")
    assert u.provider == "claude" and u.breakdown_available is True
    assert (u.input_tokens, u.output_tokens, u.cache_creation_tokens, u.cache_read_tokens) == (
        120, 55, 10, 1200)
    assert u.total_tokens == 185  # real work = in+out+cc, excludes cache reads
    assert u.total_with_cache_read == 185 + 1200
    assert u.subagent_tokens == 25 and u.main_tokens == 160  # split sums to total
    assert u.subagent_count == 1


def test_unknown_session_returns_none(svc, monkeypatch):
    monkeypatch.setattr(ss_mod.usage_cache, "scan_all",
                        lambda: Scan(events=[], sessions=0, sessions_by_project={}))
    monkeypatch.setattr(svc, "list_sessions", lambda: [])
    assert svc.get_session_tokens("nope") is None


def test_non_claude_returns_flat_total(svc, monkeypatch):
    monkeypatch.setattr(
        svc, "list_sessions",
        lambda: [_summary("codex:abc", provider="codex", total_tokens=4242)],
    )
    u = svc.get_session_tokens("codex:abc")
    assert u.provider == "codex" and u.breakdown_available is False
    assert u.total_tokens == 4242 and u.subagent_tokens == 0


# --- cost + per-subagent + timeline/anchor (priced with real pricing.py) ----

def _cev(inp, out, cc, cr, ts, uuid, sub=False, aid="", atype=""):
    return Event(sid="s1", project_dir="p", ts=ts, input=inp, output=out, cc=cc, cr=cr,
                 model="claude-opus-4-8", is_subagent=sub, agent_type=atype, uuid=uuid,
                 agent_id=aid)


def _accounting_fixture(svc, monkeypatch):
    # opus-4-8 per-Mtok: in 5, out 25, cache_write 6.25, cache_read 0.5
    events = [
        _cev(1000, 500, 0, 10000, _at(1), "u1"),                         # main A  cost .0225
        _cev(100, 50, 0, 500, _at(2), "su1", sub=True, aid="agent-x",    # subagent cost .002
             atype="general-purpose"),
        _cev(2000, 100, 0, 20000, _at(3), "u3"),                         # main B  cost .0225
    ]
    monkeypatch.setattr(ss_mod.usage_cache, "scan_all",
                        lambda: Scan(events=events, sessions=1, sessions_by_project={}))
    # session timeline: two user turns + the subagent spawn step
    tl = [
        SessionEvent(index=0, kind="user", type="user", anchor_uuid="U0",
                     timestamp=_at(0), label="start"),
        SessionEvent(index=1, kind="subagent", type="assistant", anchor_uuid="spawn1",
                     timestamp=_at(1.5), tool_use_id="spawn1",
                     subagent=SubagentRef(agent_id="agent-x", agent_type="general-purpose",
                                          tool_use_id="spawn1")),
        SessionEvent(index=2, kind="user", type="user", anchor_uuid="U2",
                     timestamp=_at(2.5), label="checkpoint"),
    ]
    monkeypatch.setattr(svc, "get_events", lambda sid, agent_id=None: tl)


def test_cost_and_per_subagent(svc, monkeypatch):
    _accounting_fixture(svc, monkeypatch)
    u = svc.get_session_tokens("s1")
    assert u.total_tokens == 3750  # (1500)+(150)+(2100)
    assert u.cost_usd == 0.047  # .0225 + .002 + .0225
    assert u.main_cost_usd == 0.045 and u.subagent_cost_usd == 0.002
    assert u.models == ["claude-opus-4-8"]
    assert u.subagent_count == 1 and len(u.subagents) == 1
    s = u.subagents[0]
    assert s.agent_id == "agent-x" and s.total_tokens == 150 and s.cost_usd == 0.002


def test_usage_at_anchor(svc, monkeypatch):
    _accounting_fixture(svc, monkeypatch)
    res = svc.get_usage_at_anchor("s1", "U2")  # cutoff t=2.5 → counts A + subagent
    assert res.found is True and res.event_count == 2
    assert res.cumulative_tokens == 1650  # 1500 + 150
    assert res.cumulative_cost_usd == 0.0245  # .0225 + .002
    missing = svc.get_usage_at_anchor("s1", "nope")
    assert missing.found is False


def test_usage_timeline(svc, monkeypatch):
    _accounting_fixture(svc, monkeypatch)
    tl = svc.get_usage_timeline("s1")
    assert tl.total_tokens == 3750 and tl.total_cost_usd == 0.047
    labels = {p.label: p for p in tl.points}
    assert labels["start"].cumulative_tokens == 0  # nothing before t=0
    assert labels["checkpoint"].cumulative_tokens == 1650  # A + subagent by t=2.5


def test_list_subagents_with_spawn_anchor(svc, monkeypatch):
    _accounting_fixture(svc, monkeypatch)
    subs = svc.list_subagents("s1")
    assert len(subs) == 1
    assert subs[0].agent_id == "agent-x" and subs[0].spawn_anchor_uuid == "spawn1"
