"""Tests for the InvestigationStore (AI/user markup + bidirectional backlinks)."""

import pytest

from muse.investigations import InvestigationStore


@pytest.fixture
def store(tmp_path):
    s = InvestigationStore(tmp_path / "muse.db")
    yield s
    s.close()


def test_create_with_refs_and_get(store):
    inv = store.create_investigation(
        "Auth retry loop",
        body="It retried npm install 6×.",
        author="ai",
        refs=[
            {"session_id": "sess-x", "anchor_uuid": "ci42", "comment": "stall point"},
            {"session_id": "sess-x", "anchor_uuid": "ci58", "label": "wrong fix"},
        ],
    )
    assert inv.id.startswith("inv_")
    assert inv.author == "ai" and len(inv.refs) == 2
    got = store.get_investigation(inv.id)
    assert got is not None
    assert got.title == "Auth retry loop"
    assert {r.anchor_uuid for r in got.refs} == {"ci42", "ci58"}


def test_backlinks_by_session(store):
    a = store.create_investigation("A", refs=[{"session_id": "s1", "anchor_uuid": "u1"}])
    store.create_investigation("B", refs=[{"session_id": "s2"}])
    store.add_reference(a.id, "s1", anchor_uuid="u9", comment="another")
    backlinks = store.get_session_references("s1")
    assert len(backlinks) == 2
    assert all(b.investigation_id == a.id for b in backlinks)
    assert all(b.investigation_title == "A" for b in backlinks)
    assert {b.ref.anchor_uuid for b in backlinks} == {"u1", "u9"}
    # s2 only has the one (anchorless) ref
    assert len(store.get_session_references("s2")) == 1
    assert store.get_session_references("nope") == []


def test_update_and_append_body(store):
    inv = store.create_investigation("T", body="first")
    store.update_investigation(inv.id, append_body="second")
    got = store.get_investigation(inv.id)
    assert got.body == "first\n\nsecond"
    store.update_investigation(inv.id, title="renamed", status="resolved")
    got = store.get_investigation(inv.id)
    assert got.title == "renamed" and got.status == "resolved"
    assert got.body == "first\n\nsecond"  # untouched by title/status update


def test_list_summaries_with_ref_count(store):
    i1 = store.create_investigation("one", refs=[{"session_id": "s"}])
    store.create_investigation("two")
    summaries = {s.id: s for s in store.list_investigations()}
    assert summaries[i1.id].ref_count == 1
    assert len(summaries) == 2


def test_delete_cascades_refs(store):
    inv = store.create_investigation("D", refs=[{"session_id": "s1", "anchor_uuid": "u1"}])
    assert store.delete_investigation(inv.id) is True
    assert store.get_investigation(inv.id) is None
    assert store.get_session_references("s1") == []  # refs gone too
    assert store.delete_investigation(inv.id) is False  # idempotent


def test_remove_reference(store):
    inv = store.create_investigation("R")
    ref = store.add_reference(inv.id, "s1", anchor_uuid="u1")
    assert ref is not None
    assert store.remove_reference(ref.id) is True
    assert store.get_session_references("s1") == []
    assert store.remove_reference("missing") is False


def test_add_reference_to_missing_investigation(store):
    assert store.add_reference("inv_nope", "s1") is None
