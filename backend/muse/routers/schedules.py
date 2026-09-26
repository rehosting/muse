"""Scheduled jobs: CRUD plus a run-now escape hatch.

The controller tick is what actually fires them (autopilot/schedule.py owns the
"is it due?" logic); this router is just the edit surface. `next_run_at` is derived
per request rather than stored, so it never drifts out of date after an edit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from ..autopilot import schedule as job_schedule
from ..models import ScheduledJob, ScheduledJobInput

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _decorate(job: ScheduledJob) -> ScheduledJob:
    """Attach the derived next-run time, and report both stamps on the SAME clock.

    Schedules are wall-clock ("07:02"), so the API answers in local time with an
    explicit offset. `last_run_at` is stored UTC; handing it back raw next to a local
    `next_run_at` made a correct 07:02 run read as 11:02 — two clocks, one row.
    """
    nxt = job_schedule.next_run(job)
    last = job_schedule.as_local(job.last_run_at)
    return job.model_copy(
        update={
            "next_run_at": nxt.isoformat() if nxt else None,
            "last_run_at": last.isoformat() if last else None,
        }
    )


def _normalize(body: ScheduledJobInput) -> ScheduledJob:
    """Validate the payload into a storable job. Day specs are accepted in the
    friendly forms ("weekdays", "mon,tue") and stored canonically as numbers."""
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is required")
    if body.every_minutes <= 0 and job_schedule.parse_at(body.at_time) is None:
        raise HTTPException(
            status_code=400,
            detail="give either every_minutes > 0 or at_time as HH:MM",
        )
    if body.every_minutes < 0:
        raise HTTPException(status_code=400, detail="every_minutes cannot be negative")
    return ScheduledJob(
        name=body.name.strip(),
        enabled=body.enabled,
        at_time=body.at_time.strip(),
        days=job_schedule.format_days(job_schedule.parse_days(body.days)),
        every_minutes=body.every_minutes,
        command=command,
        cwd=body.cwd.strip(),
        group_name=body.group_name.strip(),
        window_name=body.window_name.strip(),
    )


@router.get("")
def list_jobs(request: Request) -> list[ScheduledJob]:
    return [_decorate(j) for j in request.app.state.autopilot.store.list_jobs()]


@router.post("")
def create_job(body: ScheduledJobInput, request: Request) -> ScheduledJob:
    store = request.app.state.autopilot.store
    job = store.add_job(_normalize(body))
    store.log("schedule", "job_added", f"{job.name or job.id}: {job.command[:80]}")
    return _decorate(job)


@router.put("/{job_id}")
def update_job(job_id: int, body: ScheduledJobInput, request: Request) -> ScheduledJob:
    store = request.app.state.autopilot.store
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="no such job")
    job = store.update_job(job_id, _normalize(body))
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return _decorate(job)


@router.delete("/{job_id}")
def delete_job(job_id: int, request: Request) -> dict:
    store = request.app.state.autopilot.store
    if not store.delete_job(job_id):
        raise HTTPException(status_code=404, detail="no such job")
    store.log("schedule", "job_deleted", str(job_id))
    return {"ok": True}


@router.post("/{job_id}/run")
def run_now(job_id: int, request: Request) -> dict:
    """Fire a job immediately, ignoring its clock — for testing one without waiting
    until tomorrow morning. Stamps last_run_at like a scheduled fire would, so an
    occurrence that was already due today won't run a second time."""
    store = request.app.state.autopilot.store
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    ok, detail = job_schedule.run_job(job)
    store.mark_job_run(
        job_id, datetime.now(timezone.utc).isoformat(), "ok" if ok else f"failed: {detail}"
    )
    store.log("schedule", "job_run_now" if ok else "job_failed", f"{job.name or job_id}: {detail}")
    if not ok:
        raise HTTPException(status_code=400, detail=detail or "launch failed")
    # new_window hands back a pane id; new_session (first run of a group) doesn't.
    return {"ok": True, "pane_id": detail if detail.startswith("%") else None}
