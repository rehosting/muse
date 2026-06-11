"""Endpoints for push-notification config + test sends (ntfy)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request

from ..models import AlertEvent, AlertRules, NotifyConfig, NotifyResult

router = APIRouter(prefix="/api/notify", tags=["notify"])


def _service(request: Request):
    return request.app.state.service


@router.get("", response_model=NotifyConfig)
def get_config(request: Request) -> NotifyConfig:
    return _service(request).get_notify_config()


@router.put("", response_model=NotifyConfig)
def set_config(cfg: NotifyConfig, request: Request) -> NotifyConfig:
    return _service(request).set_notify_config(cfg)


@router.get("/rules", response_model=AlertRules)
def get_rules(request: Request) -> AlertRules:
    return _service(request).get_alert_rules()


@router.put("/rules", response_model=AlertRules)
def set_rules(rules: AlertRules, request: Request) -> AlertRules:
    return _service(request).set_alert_rules(rules)


@router.get("/log", response_model=list[AlertEvent])
def get_log(request: Request) -> list[AlertEvent]:
    return request.app.state.alerts.recent_log(50)


@router.post("/test", response_model=NotifyResult)
def send_test(request: Request, cfg: Optional[NotifyConfig] = None) -> NotifyResult:
    # Test with the posted config if given (so settings can be verified before
    # saving), else the saved one. Force enabled so a test always attempts send.
    effective = cfg or _service(request).get_notify_config()
    effective = effective.model_copy(update={"enabled": True})
    return _service(request).send_notification(
        "muse is connected — you'll get session alerts here.",
        title="muse test notification",
        tags="bell",
        config=effective,
    )
