"""get_thread_window slicing: head/tail anchors, before/after paging, around-uuid
centering, and the no-args full-thread back-compat path. The full parse is cached;
this just verifies the slice + metadata so the viewer ships a window, not 11MB."""

import pytest

from muse.config import get_settings
from muse.models import Thread, ThreadItem
from muse.services.events import EventBroker
from muse.services.session_service import SessionService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    s = SessionService(EventBroker())
    items = [
        ThreadItem(uuid=f"u{i}", role="assistant", type="assistant")
        for i in range(1000)
    ]
    monkeypatch.setattr(
        s, "get_thread", lambda sid: Thread(session_id="s1", title="t", items=list(items))
    )
    yield s
    for store in (s.store, s.search_index, s.notify_store, s.investigations):
        store.close()
    get_settings.cache_clear()


def test_no_args_returns_full_thread_annotated(svc):
    t = svc.get_thread_window("s1")
    assert len(t.items) == 1000
    assert t.total_items == 1000
    assert t.window_start == 0


def test_head_anchor(svc):
    t = svc.get_thread_window("s1", limit=400, anchor="head")
    assert t.window_start == 0
    assert [it.uuid for it in t.items[:2]] == ["u0", "u1"]
    assert len(t.items) == 400
    assert t.total_items == 1000


def test_default_anchor_follows_liveness(svc, monkeypatch):
    # Live session => tail (latest activity); finished => head (read top-down).
    monkeypatch.setattr(svc, "_is_live", lambda sid: True)
    t = svc.get_thread_window("s1", limit=400)
    assert t.window_start == 600 and t.items[-1].uuid == "u999"

    monkeypatch.setattr(svc, "_is_live", lambda sid: False)
    t = svc.get_thread_window("s1", limit=400)
    assert t.window_start == 0 and t.items[0].uuid == "u0"


def test_explicit_tail_anchor(svc):
    t = svc.get_thread_window("s1", limit=400, anchor="tail")
    assert t.window_start == 600
    assert t.items[0].uuid == "u600"
    assert t.items[-1].uuid == "u999"


def test_before_pages_upward(svc):
    t = svc.get_thread_window("s1", limit=400, before=600)
    assert t.window_start == 200
    assert t.items[0].uuid == "u200"
    assert t.items[-1].uuid == "u599"


def test_after_pages_downward(svc):
    t = svc.get_thread_window("s1", limit=400, after=600)
    assert t.window_start == 600
    assert t.items[0].uuid == "u600"
    assert len(t.items) == 400


def test_around_centers_on_uuid(svc):
    t = svc.get_thread_window("s1", limit=400, around="u500")
    assert t.window_start == 300  # 500 - 200
    assert any(it.uuid == "u500" for it in t.items)
    assert len(t.items) == 400


def test_around_unknown_uuid_falls_back_to_tail(svc):
    t = svc.get_thread_window("s1", limit=400, around="nope")
    assert t.window_start == 600
    assert t.items[-1].uuid == "u999"


def test_around_near_end_clamps(svc):
    t = svc.get_thread_window("s1", limit=400, around="u990")
    assert len(t.items) == 400
    assert t.items[-1].uuid == "u999"
    assert t.window_start == 600


def test_window_does_not_mutate_cached_thread(svc):
    full = svc.get_thread("s1")
    svc.get_thread_window("s1", limit=10, anchor="head")
    assert len(full.items) == 1000  # the cached object is untouched
