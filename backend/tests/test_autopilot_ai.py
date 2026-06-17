"""Tests for autopilot's AI idle mode: Phase A enqueues at the injection point,
Phase B re-checks the world before typing. Fake tmux + fake jobs throughout."""

from datetime import datetime, timedelta, timezone

import pytest

from muse.autopilot import controller as ctl_mod
from muse.autopilot.controller import AutopilotController
from muse.config import get_settings
from muse.models import AIJob, LiveSession

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
UPDATED = datetime(2026, 6, 12, 11, 59, tzinfo=timezone.utc)


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "muse.db"))
    get_settings.cache_clear()
    c = AutopilotController()
    yield c
    c.store.close()
    get_settings.cache_clear()


def _ls(status="idle", waiting_for=None, updated_at=UPDATED):
    return LiveSession(session_id="s1", pid=1, status=status,
                       waiting_for=waiting_for, pane_id="%5", updated_at=updated_at)


def _job(status="done", draft="carry on with step 2", created_at=None, error=None):
    return AIJob(
        id="aij_x", kind="draft_reply", params={"session_id": "s1"}, status=status,
        result={"draft": draft} if status == "done" else None, error=error,
        created_at=(created_at or NOW).isoformat(),
    )


def _wire(ctl, job=None, cost=0.0):
    ctl.enqueue_draft = lambda sid: _job(status="queued")
    ctl.get_ai_job = lambda jid: job
    ctl.ai_cost_today = lambda: cost


def _log_actions(ctl):
    return [(e.action, e.detail) for e in ctl.store.recent_log(50)]


# --- Phase A ---------------------------------------------------------------------

def test_phase_a_enqueues_and_records_pending(ctl):
    _wire(ctl)
    ctl._ai_phase_a("s1", _ls())
    job_id, req = ctl.store.get_ai_pending("s1")
    assert job_id == "aij_x" and req == UPDATED
    assert ("ai_requested", "draft job aij_x") in _log_actions(ctl)


def test_phase_a_blocked_by_budget(ctl):
    _wire(ctl, cost=99.0)  # budget default 2.0
    ctl._ai_phase_a("s1", _ls())
    assert ctl.store.get_ai_pending("s1") == (None, None)
    assert any(a == "ai_discarded" and "budget" in d for a, d in _log_actions(ctl))


def test_budget_zero_disables(ctl, monkeypatch):
    monkeypatch.setenv("MUSE_AI_DAILY_BUDGET_USD", "0")
    get_settings.cache_clear()
    _wire(ctl, cost=0.0)
    assert ctl._ai_budget_left() is False


# --- Phase B ---------------------------------------------------------------------

def test_phase_b_sends_when_world_unchanged(ctl, monkeypatch):
    sent = []
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: "normal pane")
    monkeypatch.setattr(ctl_mod.tmux, "send_text",
                        lambda p, t, submit=True: (sent.append((p, t)) or (True, "")))
    _wire(ctl, job=_job())
    ctl.store.set_ai_pending("s1", "aij_x", UPDATED)
    ctl._ai_phase_b("s1", ctl.store.get_config("s1"), _ls(), "aij_x", UPDATED, NOW)
    assert sent == [("%5", "carry on with step 2")]
    assert ctl.store.get_ai_pending("s1") == (None, None)
    assert any(a == "ai_injected" for a, _ in _log_actions(ctl))


def test_phase_b_discards_when_session_moved_on(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod.tmux, "send_text",
                        lambda *a, **k: pytest.fail("must not send"))
    _wire(ctl, job=_job())
    ctl.store.set_ai_pending("s1", "aij_x", UPDATED)
    moved = _ls(updated_at=UPDATED + timedelta(minutes=2))
    ctl._ai_phase_b("s1", ctl.store.get_config("s1"), moved, "aij_x", UPDATED, NOW)
    assert ctl.store.get_ai_pending("s1") == (None, None)
    assert any("moved on" in d for _, d in _log_actions(ctl))


def test_phase_b_discards_when_not_idle_or_waiting(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod.tmux, "send_text",
                        lambda *a, **k: pytest.fail("must not send"))
    _wire(ctl, job=_job())
    for ls in (_ls(status="busy"), _ls(waiting_for="permission")):
        ctl.store.set_ai_pending("s1", "aij_x", UPDATED)
        ctl._ai_phase_b("s1", ctl.store.get_config("s1"), ls, "aij_x", UPDATED, NOW)
        assert ctl.store.get_ai_pending("s1") == (None, None)


def test_phase_b_discards_on_rate_limit_banner(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane",
                        lambda p, n: "You've reached your usage limit. Resets at 3pm")
    monkeypatch.setattr(ctl_mod.tmux, "send_text",
                        lambda *a, **k: pytest.fail("must not send"))
    _wire(ctl, job=_job())
    ctl.store.set_ai_pending("s1", "aij_x", UPDATED)
    ctl._ai_phase_b("s1", ctl.store.get_config("s1"), _ls(), "aij_x", UPDATED, NOW)
    assert any("rate-limit" in d for _, d in _log_actions(ctl))


def test_phase_b_waits_then_expires_stale_jobs(ctl):
    fresh = _job(status="running", created_at=NOW - timedelta(minutes=2))
    _wire(ctl, job=fresh)
    ctl.store.set_ai_pending("s1", "aij_x", UPDATED)
    ctl._ai_phase_b("s1", ctl.store.get_config("s1"), _ls(), "aij_x", UPDATED, NOW)
    assert ctl.store.get_ai_pending("s1") == ("aij_x", UPDATED)  # still waiting

    stale = _job(status="running", created_at=NOW - timedelta(minutes=20))
    _wire(ctl, job=stale)
    ctl._ai_phase_b("s1", ctl.store.get_config("s1"), _ls(), "aij_x", UPDATED, NOW)
    assert ctl.store.get_ai_pending("s1") == (None, None)
    assert any("stale" in d for _, d in _log_actions(ctl))


def test_phase_b_discards_errored_job(ctl):
    _wire(ctl, job=_job(status="error", error="rate limited"))
    ctl.store.set_ai_pending("s1", "aij_x", UPDATED)
    ctl._ai_phase_b("s1", ctl.store.get_config("s1"), _ls(), "aij_x", UPDATED, NOW)
    assert ctl.store.get_ai_pending("s1") == (None, None)
    assert any("job error" in d for _, d in _log_actions(ctl))
