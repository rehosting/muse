"""Budget runway: how much headroom is left in the current 5-hour / weekly
rate-limit windows, who's burning it, and when you'll hit the wall.

This is the fleet-driving question ("can I keep all these sessions running, or
do I need to pause one?") answered cheaply enough for the phone cockpit to poll:
one pass over the (mtime-cached) usage scan, TTL-cached for _TTL seconds.

The window math mirrors stats.compute_stats: the 5h window anchors to an
OBSERVED reset when autopilot ever parsed one (reset + k*5h), else falls back to
the trailing 5 hours; the weekly window is trailing 7 days.

Budgets are honest or absent. Subscription plans (Team/Max) have no published $
caps, so unlike the stats page this deliberately does NOT use plan.py's rough
tier estimates: a budget is shown only when configured (MUSE_LIMIT_*_USD) or
OBSERVED — the window's spend the last time a limit banner was actually seen
(recorded via usage_history.record_limit_hit). Otherwise the runway reports
spend, burn, and reset time with no made-up ceiling.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import discovery
from .config import get_settings
from .models import RunwayResponse, RunwaySession, RunwayWindow
from .plan import detect_plan
from .pricing import cost_usd
from .usage_cache import scan_all

HOUR5_SECONDS = 5 * 3600
WEEK_SECONDS = 7 * 86400
_BURN_LOOKBACK_SECONDS = 30 * 60  # trailing burn-rate sample
_TTL = 10.0

_cache: tuple[float, Optional[RunwayResponse]] = (0.0, None)


def five_hour_window_start(now: datetime, history) -> tuple[datetime, str]:
    """Window start + anchor source ("reset" when derived from an observed
    usage-limit reset, else "estimated" trailing-5h)."""
    if history is not None:
        try:
            reset = history.latest_reset()
        except Exception:
            reset = None
        if reset is not None:
            k = (now - reset).total_seconds() // HOUR5_SECONDS
            start = reset + timedelta(seconds=k * HOUR5_SECONDS)
            if start <= now:
                return start, "reset"
    return now - timedelta(seconds=HOUR5_SECONDS), "estimated"


def _resolve_budget(env_value: Optional[float], history, kind: str
                    ) -> tuple[Optional[float], str]:
    """(budget, source): configured env override > observed ceiling > none."""
    if env_value is not None:
        return env_value, "configured"
    ceiling = None
    if history is not None:
        try:
            ceiling = history.observed_ceiling(kind)
        except Exception:
            ceiling = None
    if ceiling:
        return ceiling, "observed"
    return None, "none"


def compute_runway(history=None, now: Optional[datetime] = None) -> RunwayResponse:
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    plan = detect_plan(settings.limit_5h_usd, settings.limit_week_usd)
    budget_5h, src_5h = _resolve_budget(settings.limit_5h_usd, history, "5h")
    budget_week, src_week = _resolve_budget(settings.limit_week_usd, history, "week")

    start_5h, anchor_source = five_hour_window_start(now, history)
    end_5h = start_5h + timedelta(seconds=HOUR5_SECONDS)
    start_week = now - timedelta(seconds=WEEK_SECONDS)
    burn_cut = now - timedelta(seconds=_BURN_LOOKBACK_SECONDS)

    spent_5h = spent_week = burn_sample = 0.0
    per_session: dict[str, float] = {}
    for e in scan_all().events:
        if e.ts is None or e.total <= 0 or e.ts < start_week:
            continue
        cost = cost_usd(e.model, e.input, e.output, e.cc, e.cr)
        spent_week += cost
        if e.ts >= start_5h:
            spent_5h += cost
            per_session[e.sid] = per_session.get(e.sid, 0.0) + cost
        if e.ts >= burn_cut:
            burn_sample += cost

    burn_per_hour = burn_sample * (3600 / _BURN_LOOKBACK_SECONDS)

    projected: Optional[datetime] = None
    exhaust_before_reset = False
    if budget_5h:
        remaining_usd = budget_5h - spent_5h
        if remaining_usd <= 0:
            projected = now
            exhaust_before_reset = True
        elif burn_per_hour > 0:
            projected = now + timedelta(hours=remaining_usd / burn_per_hour)
            exhaust_before_reset = projected < end_5h

    titles = {s.session_id: s.title for s in discovery.list_sessions()}
    top = sorted(per_session.items(), key=lambda kv: kv[1], reverse=True)[:5]

    def window(label: str, seconds: int, start: datetime, spent: float,
               budget: Optional[float], budget_source: str, source: str) -> RunwayWindow:
        elapsed = max(0, min(seconds, int((now - start).total_seconds())))
        return RunwayWindow(
            label=label,
            window_seconds=seconds,
            anchor=start,
            anchor_source=source,
            elapsed_seconds=elapsed,
            remaining_seconds=seconds - elapsed,
            cost_usd=round(spent, 4),
            budget_usd=round(budget, 2) if budget else None,
            budget_source=budget_source,
            pct_used=round(spent / budget, 4) if budget else None,
            pct_elapsed=round(elapsed / seconds, 4),
        )

    return RunwayResponse(
        generated_at=now,
        plan_label=plan.label if plan else None,
        five_hour=window("5-hour window", HOUR5_SECONDS, start_5h, spent_5h,
                         budget_5h, src_5h, anchor_source),
        week=window("Weekly window", WEEK_SECONDS, start_week, spent_week,
                    budget_week, src_week, "estimated"),
        burn_usd_per_hour=round(burn_per_hour, 4),
        projected_exhaust_at=projected,
        exhaust_before_reset=exhaust_before_reset,
        top_sessions=[
            RunwaySession(session_id=sid, title=titles.get(sid, sid[:8]),
                          cost_usd=round(c, 4))
            for sid, c in top
        ],
    )


def get_runway(history=None) -> RunwayResponse:
    """TTL-cached wrapper — safe for the cockpit's poll cadence."""
    global _cache
    ts, cached = _cache
    if cached is not None and time.monotonic() - ts < _TTL:
        return cached
    fresh = compute_runway(history)
    _cache = (time.monotonic(), fresh)
    return fresh
