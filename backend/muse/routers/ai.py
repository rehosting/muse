"""REST surface for the AI layer (headless `claude -p` jobs).

Everything is enqueue-and-poll: POSTs return the queued AIJob immediately and
the frontend polls GET /api/ai/jobs/{id} — a job can take minutes and must
never block a request handler.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import AIJob, AIStatus

router = APIRouter(prefix="/api", tags=["ai"])


def _service(request: Request):
    return request.app.state.service


class AskRequest(BaseModel):
    question: str


class DailyDigestRequest(BaseModel):
    day: str = ""  # YYYY-MM-DD local; default = yesterday


class WeeklyRetroRequest(BaseModel):
    week_start: str = ""  # YYYY-MM-DD local Monday; default = this week's


@router.post("/ai/ask", response_model=AIJob)
def ask(body: AskRequest, request: Request) -> AIJob:
    q = body.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="question is empty")
    return _service(request).ask_muse(q)


@router.get("/ai/jobs", response_model=list[AIJob])
def list_jobs(request: Request, limit: int = 50, kind: str | None = None) -> list[AIJob]:
    return _service(request).ai_jobs.list_jobs(limit=limit, kind=kind)


@router.get("/ai/jobs/{job_id}", response_model=AIJob)
def get_job(job_id: str, request: Request) -> AIJob:
    job = _service(request).ai_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/ai/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    return {"ok": _service(request).cancel_ai_job(job_id)}


@router.post("/ai/digest/daily", response_model=AIJob)
def daily_digest(body: DailyDigestRequest, request: Request) -> AIJob:
    day = body.day or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return _service(request).enqueue_daily_digest(day)


@router.post("/ai/retro/weekly", response_model=AIJob)
def weekly_retro(body: WeeklyRetroRequest, request: Request) -> AIJob:
    week_start = body.week_start
    if not week_start:
        today = datetime.now()
        week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    return _service(request).enqueue_weekly_retro(week_start)


@router.post("/sessions/{session_id}/summarize", response_model=AIJob)
def summarize_session(session_id: str, request: Request) -> AIJob:
    job = _service(request).enqueue_session_summary(session_id)
    if job is None:
        raise HTTPException(status_code=404, detail="session not found")
    return job


@router.get("/ai/status", response_model=AIStatus)
def status(request: Request) -> AIStatus:
    return _service(request).ai_status()
