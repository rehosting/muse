"""Persistent per-day usage history: MAX-merge upserts survive transcript loss."""

from datetime import datetime

import pytest

from muse.usage_cache import Event
from muse.usage_history import UsageHistoryStore


def _ev(day_iso: str, input=100, output=50, cc=10, cr=1000, model="claude-opus-4-8",
        project="proj", agent_type=""):
    return Event(sid="s", project_dir=project,
                 ts=datetime.fromisoformat(f"{day_iso}T12:00:00+00:00"),
                 input=input, output=output, cc=cc, cr=cr, model=model,
                 is_subagent=bool(agent_type), agent_type=agent_type)


@pytest.fixture
def store(tmp_path):
    s = UsageHistoryStore(tmp_path / "muse.db")
    yield s
    s.close()


def test_roll_and_read(store):
    store.roll([_ev("2026-06-01"), _ev("2026-06-01"), _ev("2026-06-02", model="claude-haiku-4-5")])
    rows = store.rows(None, "2026-06-02")
    assert {(r["day"], r["model"]) for r in rows} == {
        ("2026-06-01", "claude-opus-4-8"), ("2026-06-02", "claude-haiku-4-5"),
    }
    d1 = next(r for r in rows if r["day"] == "2026-06-01")
    assert d1["input"] == 200 and d1["messages"] == 2 and d1["cost_usd"] > 0


def test_max_merge_survives_transcript_loss(store):
    # Full day observed: two events.
    store.roll([_ev("2026-06-01"), _ev("2026-06-01")])
    # One transcript was deleted; a later rescan only sees half the day.
    store.roll([_ev("2026-06-01")])
    row = store.rows(None, "2026-06-01")[0]
    assert row["input"] == 200  # MAX-merge kept the fuller value
    # New activity (the day grew) replaces with the larger rescan.
    store.roll([_ev("2026-06-01"), _ev("2026-06-01"), _ev("2026-06-01")])
    row = store.rows(None, "2026-06-01")[0]
    assert row["input"] == 300


def test_range_and_day_filtering(store):
    store.roll([_ev("2026-06-01"), _ev("2026-06-05"), _ev("2026-06-09")])
    assert {r["day"] for r in store.rows("2026-06-04", "2026-06-08")} == {"2026-06-05"}
    assert {r["day"] for r in store.rows(None, "2026-06-05")} == {"2026-06-01", "2026-06-05"}
    assert store.day_count() == 3


def test_agent_type_split(store):
    store.roll([_ev("2026-06-01"), _ev("2026-06-01", agent_type="Explore")])
    rows = store.rows(None, "2026-06-01")
    assert {r["agent_type"] for r in rows} == {"", "Explore"}
