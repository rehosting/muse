"""Observed limit ceilings: record/dedupe/read, and the controller's on_limit hook."""

import pytest

from muse.usage_history import UsageHistoryStore


@pytest.fixture
def store(tmp_path):
    s = UsageHistoryStore(tmp_path / "h.db")
    yield s
    s.close()


def test_record_and_ceiling(store):
    assert store.observed_ceiling("5h") is None
    assert store.record_limit_hit("5h", 62.5) is True
    assert store.observed_ceiling("5h") == 62.5
    assert store.observed_ceiling("week") is None  # kinds are independent


def test_dedupe_within_window(store):
    assert store.record_limit_hit("5h", 60.0) is True
    # The banner persists across ticks — same sighting, not a new data point.
    assert store.record_limit_hit("5h", 61.0, dedupe_minutes=30) is False
    assert store.observed_ceiling("5h") == 60.0
    # A different kind is a separate sighting.
    assert store.record_limit_hit("week", 700.0) is True


def test_ceiling_is_recent_max(store):
    store.record_limit_hit("5h", 55.0, dedupe_minutes=0)
    store.record_limit_hit("5h", 71.0, dedupe_minutes=0)
    store.record_limit_hit("5h", 63.0, dedupe_minutes=0)
    assert store.observed_ceiling("5h") == 71.0
