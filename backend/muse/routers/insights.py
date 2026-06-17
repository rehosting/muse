"""Outcome-aware analytics: GET /api/insights (shipped vs burned, heatmap,
hour matrix) and /api/insights/timeline (one project's sessions + commits)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..models import OutcomesResponse, TimelineResponse

router = APIRouter(prefix="/api", tags=["insights"])


def _service(request: Request):
    return request.app.state.service


@router.get("/insights", response_model=OutcomesResponse)
def insights(request: Request, days: int = 30) -> OutcomesResponse:
    if days not in (0, 7, 30, 90):
        raise HTTPException(status_code=400, detail="days must be one of 0,7,30,90")
    return _service(request).get_insights(days)


@router.get("/insights/timeline", response_model=TimelineResponse)
def timeline(
    request: Request,
    project: str = Query(..., min_length=1),
    days: int = 30,
) -> TimelineResponse:
    if days not in (7, 30, 90):
        raise HTTPException(status_code=400, detail="days must be one of 7,30,90")
    return _service(request).get_insights_timeline(project, days)
