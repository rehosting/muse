"""Detect the user's Claude subscription/plan from ~/.claude.json (read-only).

The exact 5-hour / weekly usage caps are not stored on disk (Anthropic doesn't
publish hard token/$ numbers), so we map the detected rate-limit tier to rough
USD budget *estimates* for the pace charts. These are overridable via
MUSE_LIMIT_5H_USD / MUSE_LIMIT_WEEK_USD.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Plan

# rate_limit_tier -> (friendly label, est. 5-hour USD, est. weekly USD)
_TIER_BUDGETS: dict[str, tuple[str, float, float]] = {
    "default_claude_pro": ("Pro", 30.0, 180.0),
    "default_claude_max_5x": ("Max 5×", 150.0, 900.0),
    "default_claude_max_20x": ("Max 20×", 600.0, 3600.0),
}

_ORG_LABELS = {
    "claude_team": "Claude Team",
    "claude_max": "Claude Max",
    "claude_pro": "Claude Pro",
}


def _read_account() -> tuple[Optional[dict], Optional[str]]:
    path = Path.home() / ".claude.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    return data.get("oauthAccount"), data.get("cachedExtraUsageDisabledReason")


def detect_plan(limit_5h_env: Optional[float], limit_week_env: Optional[float]) -> Optional[Plan]:
    acc, extra_disabled = _read_account()

    tier = acc.get("userRateLimitTier") if acc else None
    tier_label, est_5h, est_week = _TIER_BUDGETS.get(tier or "", (None, None, None))

    org_type = acc.get("organizationType") if acc else None
    org_pretty = _ORG_LABELS.get(org_type or "", org_type or "Claude")
    label = f"{org_pretty} · {tier_label}" if tier_label else org_pretty

    # Budget resolution: explicit env override wins; else tier estimate; else none.
    if limit_5h_env is not None or limit_week_env is not None:
        b5 = limit_5h_env if limit_5h_env is not None else est_5h
        bw = limit_week_env if limit_week_env is not None else est_week
        source = "configured"
    elif est_5h is not None or est_week is not None:
        b5, bw, source = est_5h, est_week, "estimated"
    else:
        b5 = bw = None
        source = "none"

    if acc is None and source == "none":
        return None

    return Plan(
        label=label,
        organization_name=acc.get("organizationName") if acc else None,
        organization_type=org_type,
        seat_tier=acc.get("seatTier") if acc else None,
        rate_limit_tier=tier,
        has_extra_usage=bool(acc.get("hasExtraUsageEnabled")) if acc else False,
        extra_usage_disabled_reason=extra_disabled,
        budget_source=source,
        five_hour_budget_usd=b5,
        weekly_budget_usd=bw,
    )
