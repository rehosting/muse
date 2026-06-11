"""Reference validation: a mis-transcribed session_id / anchor can't be stored as
a dead deep-link — it's rejected so the agent corrects it."""

import pytest

from muse.config import get_settings
from muse.models import SessionEvent
from muse.services.events import EventBroker
from muse.services.session_service import SessionService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    s = SessionService(EventBroker())

    def fake_events(sid, agent_id=None):
        if sid == "good":
            return [
                SessionEvent(index=0, kind="user", type="user", anchor_uuid="u1"),
                SessionEvent(index=1, kind="tool_call", type="assistant",
                             anchor_uuid="a1", tool_use_id="t1"),
            ]
        return None  # unknown session

    monkeypatch.setattr(s, "get_events", fake_events)
    yield s
    for store in (s.store, s.search_index, s.notify_store, s.investigations):
        store.close()
    get_settings.cache_clear()


def test_valid_ref_accepted(svc):
    inv = svc.create_investigation("ok", refs=[{"session_id": "good", "anchor_uuid": "a1"}])
    assert len(inv.refs) == 1
    # tool_use_id also counts as a valid anchor
    svc.create_investigation("ok2", refs=[{"session_id": "good", "anchor_uuid": "t1"}])
    # session-level ref (no anchor) is fine too
    svc.create_investigation("ok3", refs=[{"session_id": "good"}])


def test_unknown_session_rejected(svc):
    with pytest.raises(ValueError, match="did not resolve to a session"):
        svc.create_investigation("bad", refs=[{"session_id": "nope", "anchor_uuid": "u1"}])


def test_bad_anchor_rejected(svc):
    with pytest.raises(ValueError, match="not a step"):
        svc.create_investigation("bad", refs=[{"session_id": "good", "anchor_uuid": "ZZZ"}])


def test_near_miss_session_suggested(svc, monkeypatch):
    """A one-character mis-transcription (43ac↔43a3) gets a 'did you mean' hint."""
    real = "e1a487de-fbc9-43ac-8210-03d9bb462bdf"
    wrong = "e1a487de-fbc9-43a3-8210-03d9bb462bdf"
    monkeypatch.setattr(
        svc, "list_sessions", lambda: [type("S", (), {"session_id": real})()]
    )
    with pytest.raises(ValueError, match=f"did you mean '{real}'"):
        svc.create_investigation("bad", refs=[{"session_id": wrong}])


def test_add_reference_validates(svc):
    inv = svc.create_investigation("base")
    assert svc.add_reference(inv.id, "good", "a1") is not None
    with pytest.raises(ValueError):
        svc.add_reference(inv.id, "good", "ZZZ")
    with pytest.raises(ValueError):
        svc.add_reference(inv.id, "nope", "a1")
