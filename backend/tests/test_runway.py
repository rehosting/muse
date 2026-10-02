"""Runway computation: window anchoring, spend sums, burn projection, and the
honest budget policy (configured > observed ceiling > none — never estimates)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from muse import runway
from muse.usage_cache import Event, Scan

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _event(sid, minutes_ago, output=100_000):
    return Event(
        sid=sid, project_dir="-p", ts=NOW - timedelta(minutes=minutes_ago),
        input=1000, output=output, cc=0, cr=0,
        model="claude-opus-4-8", is_subagent=False, agent_type="",
    )


class FakeHistory:
    def __init__(self, reset=None, ceilings=None):
        self._reset = reset
        self._ceilings = ceilings or {}

    def latest_reset(self):
        return self._reset

    def observed_ceiling(self, kind="5h", days=30):
        return self._ceilings.get(kind)


def _settings(limit_5h=None, limit_week=None):
    return SimpleNamespace(limit_5h_usd=limit_5h, limit_week_usd=limit_week)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    events = [
        _event("hot", 10),      # in 5h window + burn sample
        _event("hot", 20),
        _event("warm", 60),     # in 5h window only
        _event("old", 60 * 26), # weekly only
        _event("ancient", 60 * 24 * 8),  # outside every window
    ]
    monkeypatch.setattr(runway, "scan_all", lambda: Scan(events, 5, {"-p": 5}))
    monkeypatch.setattr(runway.discovery, "list_sessions", lambda: [])
    monkeypatch.setattr(runway, "get_settings", _settings)
    monkeypatch.setattr(
        runway, "detect_plan",
        lambda a, b: type("P", (), {"label": "Claude Team · Max 5×"})(),
    )
    runway._cache = (0.0, None)


def test_window_sums_and_top_sessions():
    r = runway.compute_runway(history=None, now=NOW)
    per_event = r.five_hour.cost_usd / 3  # 3 events in the 5h window, equal cost
    assert per_event > 0
    assert r.week.cost_usd == pytest.approx(per_event * 4)
    assert [s.session_id for s in r.top_sessions] == ["hot", "warm"]
    assert r.five_hour.anchor_source == "estimated"
    assert r.plan_label == "Claude Team · Max 5×"


def test_subscription_without_calibration_has_no_budget():
    # No env override, no observed limit hit → no made-up ceiling, no projection.
    r = runway.compute_runway(history=FakeHistory(), now=NOW)
    for w in (r.five_hour, r.week):
        assert w.budget_usd is None and w.pct_used is None and w.budget_source == "none"
    assert r.projected_exhaust_at is None and r.exhaust_before_reset is False
    assert r.burn_usd_per_hour > 0  # burn is always reported


def test_observed_ceiling_becomes_budget():
    hist = FakeHistory(ceilings={"5h": 80.0, "week": 500.0})
    r = runway.compute_runway(history=hist, now=NOW)
    assert r.five_hour.budget_usd == 80.0 and r.five_hour.budget_source == "observed"
    assert r.week.budget_usd == 500.0 and r.week.budget_source == "observed"
    assert r.five_hour.pct_used == pytest.approx(r.five_hour.cost_usd / 80.0, abs=1e-3)
    assert r.projected_exhaust_at is not None


def test_configured_env_beats_observed(monkeypatch):
    monkeypatch.setattr(runway, "get_settings", lambda: _settings(limit_5h=42.0))
    r = runway.compute_runway(history=FakeHistory(ceilings={"5h": 80.0}), now=NOW)
    assert r.five_hour.budget_usd == 42.0 and r.five_hour.budget_source == "configured"


def test_observed_reset_anchors_window():
    # Reset observed 7h ago → current window began 2h ago (reset + 1*5h).
    r = runway.compute_runway(history=FakeHistory(reset=NOW - timedelta(hours=7)), now=NOW)
    assert r.five_hour.anchor_source == "reset"
    assert r.five_hour.anchor == NOW - timedelta(hours=2)
    assert r.five_hour.elapsed_seconds == 2 * 3600
    assert r.five_hour.cost_usd > 0


def test_burn_projection_with_budget(monkeypatch):
    monkeypatch.setattr(runway, "get_settings", lambda: _settings(limit_5h=150.0))
    r = runway.compute_runway(history=None, now=NOW)
    # 2 events in the last 30 min → burn = 2*cost/0.5h
    assert r.burn_usd_per_hour == pytest.approx((r.five_hour.cost_usd / 3) * 4, rel=1e-6)
    assert r.projected_exhaust_at is not None
    # cheap events: exhaustion lands far beyond this window
    assert r.exhaust_before_reset is False


def test_exhausted_budget_projects_now(monkeypatch):
    monkeypatch.setattr(runway, "get_settings", lambda: _settings(limit_5h=0.01))
    r = runway.compute_runway(history=None, now=NOW)
    assert r.exhaust_before_reset is True
    assert r.projected_exhaust_at == NOW


def test_ttl_cache(monkeypatch):
    calls = []
    orig = runway.compute_runway
    monkeypatch.setattr(runway, "compute_runway",
                        lambda history=None, now=None: calls.append(1) or orig(history, now))
    runway.get_runway()
    runway.get_runway()
    assert len(calls) == 1
