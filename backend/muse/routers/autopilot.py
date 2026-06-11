"""Autopilot endpoints: state, arm, per-session config, manual send."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import AutopilotState

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])


class ArmBody(BaseModel):
    armed: bool


class PolicyBody(BaseModel):
    session_ids: list[str]
    enabled: bool = False
    idle_mode: str = "message"
    message: str = ""
    max_sends: int = 5
    interval_seconds: int = 30
    context_threshold_pct: int = 80
    context_action: str = "compact"
    context_message: str = ""
    backoff_seconds: int = 900


class ScheduleBody(BaseModel):
    enabled: bool = False
    start_hour: int = 22
    end_hour: int = 7


def _ctl(request: Request):
    return request.app.state.autopilot


@router.get("", response_model=AutopilotState)
def get_state(request: Request) -> AutopilotState:
    return _ctl(request).get_state()


@router.post("/arm", response_model=AutopilotState)
def arm(body: ArmBody, request: Request) -> AutopilotState:
    return _ctl(request).set_armed(body.armed)


@router.post("/schedule", response_model=AutopilotState)
def set_schedule(body: ScheduleBody, request: Request) -> AutopilotState:
    return _ctl(request).set_schedule(body.enabled, body.start_hour, body.end_hour)


@router.post("/policy", response_model=AutopilotState)
def apply_policy(body: PolicyBody, request: Request) -> AutopilotState:
    if not body.session_ids:
        raise HTTPException(status_code=400, detail="no sessions selected")
    policy = body.model_dump(exclude={"session_ids"})
    policy["max_sends"] = max(1, policy["max_sends"])
    policy["interval_seconds"] = max(5, policy["interval_seconds"])
    policy["context_threshold_pct"] = min(100, max(1, policy["context_threshold_pct"]))
    policy["backoff_seconds"] = max(30, policy["backoff_seconds"])
    return _ctl(request).apply_policy(body.session_ids, policy)


@router.post("/sessions/{session_id}/send")
def manual_send(session_id: str, request: Request) -> dict:
    ok, detail = _ctl(request).manual_send(session_id)
    if not ok:
        raise HTTPException(status_code=400, detail=detail or "send failed")
    return {"ok": True}
