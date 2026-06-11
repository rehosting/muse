"""Tests for the WorklogStore (lightweight running notes + journal grouping)."""

import pytest

from muse.worklog import WorklogStore, _today


@pytest.fixture
def store(tmp_path):
    s = WorklogStore(tmp_path / "muse.db")
    yield s
    s.close()


def test_create_and_get(store):
    n = store.create_note("trying the WAL fix now", session_id="s1", anchor_uuid="u1")
    assert n.id.startswith("note_")
    assert n.kind == "note" and n.author == "user"
    assert n.day == _today()
    got = store.get_note(n.id)
    assert got is not None and got.body == "trying the WAL fix now"
    assert got.session_id == "s1" and got.anchor_uuid == "u1"


def test_global_note_without_session(store):
    n = store.create_note("random 3pm thought")
    assert n.session_id is None and n.anchor_uuid is None


def test_invalid_kind_and_author_fall_back(store):
    n = store.create_note("x", kind="bogus", author="martian")
    assert n.kind == "note" and n.author == "user"


def test_list_filters(store):
    store.create_note("a", session_id="s1")
    store.create_note("b", session_id="s2", kind="next")
    store.create_note("c")
    assert {n.body for n in store.list_notes()} == {"a", "b", "c"}
    assert [n.body for n in store.list_notes(session_id="s1")] == ["a"]
    assert [n.body for n in store.list_notes(kind="next")] == ["b"]
    assert {n.body for n in store.list_notes(day=_today())} == {"a", "b", "c"}
    assert store.list_notes(day="1999-01-01") == []


def test_sessions_with_open_next(store):
    store.create_note("follow up", session_id="s1", kind="next")
    store.create_note("plain", session_id="s2")
    store.create_note("global next", kind="next")  # no session — not an open loop
    assert store.sessions_with_open_next() == {"s1"}


def test_update_note(store):
    n = store.create_note("draft", kind="next")
    got = store.update_note(n.id, body="final", kind="note")
    assert got is not None and got.body == "final" and got.kind == "note"
    # invalid kind keeps the existing one
    got = store.update_note(n.id, kind="bogus")
    assert got.kind == "note"
    assert store.update_note("note_missing", body="x") is None


def test_delete_note(store):
    n = store.create_note("temp")
    assert store.delete_note(n.id) is True
    assert store.get_note(n.id) is None
    assert store.delete_note(n.id) is False
