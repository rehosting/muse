"""Context packs: rendered hand-off markdown under ~/.muse/packs (muse-owned)."""

from pathlib import Path

import pytest

from muse.packs import PackStore


@pytest.fixture
def store(tmp_path):
    s = PackStore(tmp_path / "muse.db", tmp_path / "packs")
    yield s
    s.close()


def test_create_writes_file_and_row(store, tmp_path):
    p = store.create("Handoff", "# Context\n\nstuff", "sess-1")
    assert p.id.startswith("pk_")
    f = Path(p.path)
    assert f.is_file() and f.parent == tmp_path / "packs"
    assert f.read_text() == "# Context\n\nstuff"
    got = store.get(p.id)
    assert got is not None and got.source_session_id == "sess-1"
    assert [x.id for x in store.list()] == [p.id]


def test_delete_removes_row_and_file(store):
    p = store.create("t", "body")
    assert store.delete(p.id) is True
    assert store.get(p.id) is None
    assert not Path(p.path).exists()
    assert store.delete(p.id) is False
