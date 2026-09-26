"""Scheduled jobs: when a job is due (the part with edge cases), and the CRUD API."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse.autopilot import schedule as sched
from muse.autopilot.store import AutopilotStore
from muse.models import ScheduledJob
from muse.routers import schedules as schedules_router


def local(y, mo, d, h, mi):
    """A local-time instant — schedules are wall-clock, not UTC."""
    return datetime(y, mo, d, h, mi).astimezone()


def utc_iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def weekday_job(**kw):
    base = dict(id=1, name="hello", enabled=True, at_time="07:02", days="0,1,2,3,4",
                command="claude --model haiku 'hello world'")
    base.update(kw)
    return ScheduledJob(**base)


# --- day specs --------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("weekdays", [0, 1, 2, 3, 4]),
        ("weekends", [5, 6]),
        ("mon,wed,fri", [0, 2, 4]),
        ("0,1,2,3,4", [0, 1, 2, 3, 4]),
        ("Monday, Thursday", [0, 3]),
        ("", [0, 1, 2, 3, 4, 5, 6]),
        ("nonsense", [0, 1, 2, 3, 4, 5, 6]),  # unparseable → every day, never silently never
    ],
)
def test_parse_days(spec, expected):
    assert sched.parse_days(spec) == expected


@pytest.mark.parametrize("bad", ["", "7:02", "25:00", "07:99", "0702", "07:02:00"])
def test_parse_at_rejects_malformed(bad):
    if bad == "7:02":
        assert sched.parse_at(bad) == (7, 2)  # single-digit hour is fine
    else:
        assert sched.parse_at(bad) is None


# --- due_at -----------------------------------------------------------------------


def test_due_on_a_weekday_after_the_scheduled_minute():
    # Wednesday 2026-09-16 07:03, job at 07:02 — due, and the occurrence is 07:02.
    occ = sched.due_at(weekday_job(), now=local(2026, 9, 16, 7, 3))
    assert occ == local(2026, 9, 16, 7, 2)


def test_not_due_before_its_time():
    assert sched.due_at(weekday_job(), now=local(2026, 9, 16, 7, 1)) is None


def test_not_due_on_the_weekend():
    # Saturday and Sunday, well after 07:02.
    assert sched.due_at(weekday_job(), now=local(2026, 9, 19, 9, 0)) is None
    assert sched.due_at(weekday_job(), now=local(2026, 9, 20, 9, 0)) is None


def test_does_not_fire_twice_for_the_same_occurrence():
    job = weekday_job(last_run_at=utc_iso(local(2026, 9, 16, 7, 2)))
    assert sched.due_at(job, now=local(2026, 9, 16, 7, 30)) is None
    # ...but the next weekday's occurrence is a fresh one.
    assert sched.due_at(job, now=local(2026, 9, 17, 7, 2)) == local(2026, 9, 17, 7, 2)


def test_missed_occurrence_is_abandoned_past_the_grace_window():
    # Machine asleep until the afternoon: a 07:02 job must not start at 15:00.
    late = local(2026, 9, 16, 7, 2) + timedelta(minutes=sched.DAILY_GRACE_MINUTES + 1)
    assert sched.due_at(weekday_job(), now=late) is None
    # Just inside the window it still fires (muse restarted a few minutes late).
    inside = local(2026, 9, 16, 7, 2) + timedelta(minutes=sched.DAILY_GRACE_MINUTES - 1)
    assert sched.due_at(weekday_job(), now=inside) is not None


def test_disabled_job_is_never_due():
    assert sched.due_at(weekday_job(enabled=False), now=local(2026, 9, 16, 7, 3)) is None


def test_interval_job_fires_then_waits():
    job = ScheduledJob(id=2, every_minutes=30, command="echo hi")
    assert sched.due_at(job, now=local(2026, 9, 16, 7, 0)) is not None  # never run → now
    job.last_run_at = utc_iso(local(2026, 9, 16, 7, 0))
    assert sched.due_at(job, now=local(2026, 9, 16, 7, 29)) is None
    assert sched.due_at(job, now=local(2026, 9, 16, 7, 30)) is not None


def test_next_run_skips_to_monday_from_a_friday_evening():
    nxt = sched.next_run(weekday_job(), now=local(2026, 9, 18, 20, 0))  # Friday
    assert nxt == local(2026, 9, 21, 7, 2)  # Monday
    assert nxt.weekday() == 0


# --- store + API ------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = AutopilotStore(tmp_path / "muse.db")
    app = FastAPI()
    app.include_router(schedules_router.router)
    app.state.autopilot = type("AP", (), {"store": store})()
    return TestClient(app), store


def test_crud_roundtrip_and_day_normalization(client):
    c, _ = client
    r = c.post("/api/schedules", json={
        "name": "hello", "at_time": "07:02", "days": "weekdays",
        "command": "claude --model haiku 'hello world'",
    })
    assert r.status_code == 200
    job = r.json()
    assert job["days"] == "0,1,2,3,4"  # friendly spec stored canonically
    assert job["next_run_at"]  # derived per request

    listed = c.get("/api/schedules").json()
    assert [j["id"] for j in listed] == [job["id"]]

    r = c.put(f"/api/schedules/{job['id']}", json={
        "name": "hello", "at_time": "08:15", "days": "mon,fri",
        "command": "echo hi", "enabled": False,
    })
    assert r.json()["days"] == "0,4" and r.json()["enabled"] is False
    assert r.json()["next_run_at"] is None  # disabled → nothing scheduled

    assert c.delete(f"/api/schedules/{job['id']}").status_code == 200
    assert c.get("/api/schedules").json() == []


def test_rejects_a_job_with_no_clock(client):
    c, _ = client
    r = c.post("/api/schedules", json={"command": "echo hi"})
    assert r.status_code == 400  # neither at_time nor every_minutes


def test_rejects_an_empty_command(client):
    c, _ = client
    r = c.post("/api/schedules", json={"at_time": "07:02", "command": "   "})
    assert r.status_code == 400


def test_run_now_stamps_the_job_so_it_will_not_double_fire(client, monkeypatch):
    c, store = client
    monkeypatch.setattr(sched.tmux, "available", lambda: True)
    monkeypatch.setattr(sched.tmux, "list_layout", lambda: [])
    monkeypatch.setattr(sched.tmux, "new_session", lambda *a, **k: (True, ""))
    job = c.post("/api/schedules", json={
        "at_time": "07:02", "days": "weekdays", "command": "echo hi",
    }).json()
    assert c.post(f"/api/schedules/{job['id']}/run").status_code == 200
    assert store.get_job(job["id"]).last_run_at is not None


def test_missing_job_is_404(client):
    c, _ = client
    assert c.post("/api/schedules/999/run").status_code == 404
    assert c.delete("/api/schedules/999").status_code == 404


# --- controller integration -------------------------------------------------------


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    from muse.autopilot.controller import AutopilotController
    from muse.config import get_settings

    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "muse.db"))
    get_settings.cache_clear()
    c = AutopilotController()
    yield c
    c.store.close()
    get_settings.cache_clear()


def test_tick_launches_a_due_job_once(ctl, monkeypatch):
    launched = []
    monkeypatch.setattr(sched.tmux, "available", lambda: True)
    monkeypatch.setattr(sched.tmux, "list_layout", lambda: [])
    monkeypatch.setattr(
        sched.tmux, "new_session",
        lambda name, cwd=None, command=None, window_name=None:
        launched.append((name, command)) or (True, ""),
    )
    # An interval job is due immediately (never run), so the tick must fire it...
    job = ctl.store.add_job(ScheduledJob(every_minutes=60, name="hi", command="echo hi"))
    ctl._run_due_jobs()
    assert len(launched) == 1
    # ...and not again on the next tick, because last_run_at now covers it.
    ctl._run_due_jobs()
    assert len(launched) == 1
    assert ctl.store.get_job(job.id).last_status == "ok"


def test_a_failing_job_is_stamped_not_retried_every_tick(ctl, monkeypatch):
    attempts = []
    monkeypatch.setattr(sched.tmux, "available", lambda: True)
    monkeypatch.setattr(sched.tmux, "list_layout", lambda: [])
    monkeypatch.setattr(
        sched.tmux, "new_session",
        lambda *a, **k: attempts.append(1) or (False, "tmux exploded"),
    )
    job = ctl.store.add_job(ScheduledJob(every_minutes=60, name="bad", command="nope"))
    ctl._run_due_jobs()
    ctl._run_due_jobs()
    assert len(attempts) == 1  # the stamp lands before the launch, so no retry storm
    assert "failed" in ctl.store.get_job(job.id).last_status


def test_disabled_jobs_are_skipped_by_the_tick(ctl, monkeypatch):
    monkeypatch.setattr(sched.tmux, "available", lambda: True)
    monkeypatch.setattr(
        sched.tmux, "new_session", lambda *a, **k: pytest.fail("must not launch")
    )
    ctl.store.add_job(ScheduledJob(every_minutes=1, enabled=False, command="echo hi"))
    ctl._run_due_jobs()


def test_api_reports_both_stamps_on_the_local_clock(client, monkeypatch):
    """A 07:02 run stored as 11:02Z must not read back as 11:02 beside a local
    next_run_at — same row, same clock, or a correct schedule looks four hours off."""
    c, store = client
    monkeypatch.setattr(sched.tmux, "available", lambda: True)
    monkeypatch.setattr(sched.tmux, "list_layout", lambda: [])
    monkeypatch.setattr(sched.tmux, "new_session", lambda *a, **k: (True, ""))
    job = c.post("/api/schedules", json={
        "at_time": "07:02", "days": "weekdays", "command": "echo hi",
    }).json()
    ran_at = local(2026, 9, 21, 7, 2)
    store.mark_job_run(job["id"], utc_iso(ran_at), "ok")

    got = c.get("/api/schedules").json()[0]
    assert datetime.fromisoformat(got["last_run_at"]) == ran_at
    assert got["last_run_at"].startswith("2026-09-21T07:02")  # local wall clock, not 11:02Z
    # ...and the stored value is still UTC (display conversion only).
    assert store.get_job(job["id"]).last_run_at.endswith("+00:00")
