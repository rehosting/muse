"""Scheduled jobs: launch a tmux window on a clock, not on a session's behaviour.

The autopilot watches live sessions and reacts to them. This is the other direction —
"at 07:02 on weekdays, start this" — so the pieces are deliberately separate: this
module is pure (given a job and the current time, is it due?), the store owns the rows,
`run_job` does the tmux side effect, and the controller tick glues them together.

Two shapes, both expressed by one row:

  * daily    — `at_time` "HH:MM" in LOCAL time on the weekdays in `days` (Mon=0).
  * interval — every `every_minutes` minutes, measured from the last run.

A missed daily run (laptop asleep, muse down) fires late only inside
`DAILY_GRACE_MINUTES`; past that the occurrence is abandoned rather than starting a
07:02 job at three in the afternoon.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..models import ScheduledJob
from . import tmux

# How late a daily occurrence may still fire after its scheduled minute.
DAILY_GRACE_MINUTES = 60
# tmux session scheduled jobs land in when the job doesn't name one.
DEFAULT_GROUP = "jobs"

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # index == Python weekday()


def parse_days(spec: str) -> list[int]:
    """Parse a day spec into weekday numbers (Mon=0). Accepts names or numbers, plus
    the shorthands "weekdays"/"weekends"/"daily". Unknown tokens are ignored; an empty
    result means every day."""
    text = (spec or "").strip().lower()
    if not text or text in ("daily", "every", "all"):
        return list(range(7))
    if text == "weekdays":
        return [0, 1, 2, 3, 4]
    if text == "weekends":
        return [5, 6]
    out: list[int] = []
    for token in text.replace(" ", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit() and 0 <= int(token) <= 6:
            out.append(int(token))
        elif token[:3] in WEEKDAYS:
            out.append(WEEKDAYS.index(token[:3]))
    return sorted(set(out)) or list(range(7))


def format_days(days: list[int]) -> str:
    """Canonical storage form: comma-separated weekday numbers."""
    return ",".join(str(d) for d in sorted(set(days)))


def parse_at(at_time: str) -> Optional[tuple[int, int]]:
    """"HH:MM" → (hour, minute), or None when malformed."""
    parts = (at_time or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def as_local(ts: Optional[str]) -> Optional[datetime]:
    """Parse a stored UTC timestamp into local time (schedules are wall-clock)."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def due_at(job: ScheduledJob, now: Optional[datetime] = None) -> Optional[datetime]:
    """The occurrence this job is currently owed, or None if it isn't due.

    Returning the occurrence (rather than a bool) is what makes a daily job fire once:
    the caller compares it against `last_run_at`, so a second tick in the same minute —
    or a restart — can't double-fire it.
    """
    if not job.enabled:
        return None
    now = now or datetime.now().astimezone()
    last = as_local(job.last_run_at)

    if job.every_minutes > 0:
        if last is None:
            return now
        return now if now - last >= timedelta(minutes=job.every_minutes) else None

    hhmm = parse_at(job.at_time)
    if hhmm is None:
        return None
    hour, minute = hhmm
    occurrence = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if occurrence > now:  # today's slot hasn't arrived yet
        return None
    if occurrence.weekday() not in parse_days(job.days):
        return None
    if now - occurrence > timedelta(minutes=DAILY_GRACE_MINUTES):
        return None  # too late — skip this occurrence rather than fire it hours off
    if last is not None and last >= occurrence:
        return None  # already ran this occurrence
    return occurrence


def next_run(job: ScheduledJob, now: Optional[datetime] = None) -> Optional[datetime]:
    """When this job is expected to fire next (local), for display. None when disabled
    or unschedulable."""
    if not job.enabled:
        return None
    now = now or datetime.now().astimezone()
    if job.every_minutes > 0:
        last = as_local(job.last_run_at)
        return now if last is None else last + timedelta(minutes=job.every_minutes)
    hhmm = parse_at(job.at_time)
    if hhmm is None:
        return None
    hour, minute = hhmm
    days = parse_days(job.days)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for _ in range(8):
        if candidate > now and candidate.weekday() in days:
            return candidate
        candidate += timedelta(days=1)
    return None


def run_job(job: ScheduledJob) -> tuple[bool, str]:
    """Launch the job's command in a new tmux window. Creates the target group (tmux
    session) if it doesn't exist yet, exactly like session-restore does, so the first
    run of the day doesn't need a group sitting there waiting."""
    if not tmux.available():
        return False, "tmux is not running"
    cwd = job.cwd if job.cwd and os.path.isdir(job.cwd) else os.path.expanduser("~")
    group = job.group_name or DEFAULT_GROUP
    window = job.window_name or job.name or "job"
    existing = {p["session_name"] for p in tmux.list_layout()}
    if group in existing:
        return tmux.new_window(cwd, job.command, session=group, name=window)
    ok, err = tmux.new_session(group, cwd=cwd, command=job.command, window_name=window)
    return ok, (err if not ok else "")
