"""Aggregate token usage / cost across all sessions, with rolling windows.

Powers the stats page: overall usage, per-model breakdown, a 5-hour window and
a 7-day window (each with a time-progress anchor), and 7 days of daily buckets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import discovery
from .claude_stats import load_claude_stats
from .config import get_settings
from .models import (
    AgentTypeStat,
    Bucket,
    CostBreakdown,
    DailyStat,
    HourStat,
    ModelStat,
    ProjectStat,
    StatsResponse,
    ToolCount,
    Totals,
    TopSession,
    WindowStat,
)
from .paths import decode_cwd
from .plan import detect_plan
from .pricing import cost_usd, price_for
from .usage_cache import scan_all
from .usage_insights import compute_insights

HOUR5_SECONDS = 5 * 3600
WEEK_SECONDS = 7 * 86400
WINDOW_BUCKETS = 24  # number of buckets across each window for the pace chart


class _Acc:
    def __init__(self) -> None:
        self.input = 0
        self.output = 0
        self.cc = 0
        self.cr = 0
        self.messages = 0
        self.cost = 0.0
        self.anchor: Optional[datetime] = None

    def add(self, i, o, cc, cr, cost, ts: Optional[datetime]) -> None:
        self.input += i
        self.output += o
        self.cc += cc
        self.cr += cr
        self.cost += cost
        self.messages += 1
        if ts and (self.anchor is None or ts < self.anchor):
            self.anchor = ts

    @property
    def total(self) -> int:
        return self.input + self.output + self.cc + self.cr


def compute_stats(days: int = 0, history=None) -> StatsResponse:
    """`days` selects the reporting range (0 = all time; 1/7/30/90). Totals and
    breakdowns for days BEFORE today come from the persistent usage_daily
    history (survives transcript deletion); today always comes from the live
    scan. The 5h/weekly windows and insights ignore the range (always trailing).
    """
    now = datetime.now(timezone.utc)
    settings = get_settings()
    plan = detect_plan(settings.limit_5h_usd, settings.limit_week_usd)
    budget_5h = plan.five_hour_budget_usd if plan else settings.limit_5h_usd
    budget_week = plan.weekly_budget_usd if plan else settings.limit_week_usd

    # Accurate titles + project paths (the dir-name decode is lossy).
    summaries = discovery.list_sessions()
    title_by_sid = {s.session_id: s.title for s in summaries}
    cwd_by_dir = {s.project_dir: s.project_cwd for s in summaries if s.project_cwd}

    scan = scan_all()
    sessions = scan.sessions

    totals = _Acc()
    by_model: dict[str, _Acc] = {}
    hours = _Acc()
    week = _Acc()
    # (timestamp, cost, tokens) per event, for bucketing into pace charts.
    hours_events: list[tuple[datetime, float, int]] = []
    week_events: list[tuple[datetime, float, int]] = []

    cost_in = cost_out = cost_cw = cost_cr = 0.0
    cache_savings = 0.0
    tool_counts: dict[str, int] = {}
    per_session: dict[str, list[float]] = {}  # sid -> [cost, tokens, messages]
    per_project: dict[str, list[float]] = {}  # dir -> [cost, tokens, messages, sessions]
    for name, cnt in scan.sessions_by_project.items():
        per_project[name] = [0.0, 0, 0, cnt]
    by_hour = [[0, 0.0] for _ in range(24)]  # [messages, cost] per LOCAL hour

    # Pre-seed the daily buckets (LOCAL days — "today" must mean the user's
    # today, not UTC's) so the chart always has every day in range.
    local_now = now.astimezone()
    today_key = local_now.strftime("%Y-%m-%d")
    chart_days = {0: 90, 1: 7, 7: 7, 30: 30, 90: 90}.get(days, 7)
    daily: dict[str, list[float]] = {}
    for d in range(chart_days - 1, -1, -1):
        key = (local_now - timedelta(days=d)).strftime("%Y-%m-%d")
        daily[key] = [0, 0.0]

    # Reporting-range cutoffs. Totals/breakdowns for past days come from the
    # usage_daily history; the live scan contributes only TODAY to them (the
    # seam rule that prevents double counting). Event-granular sections
    # (tools, top sessions, by-hour) can't come from history — they use the
    # live events with the range cutoff (best-effort beyond transcript
    # retention).
    range_start_day = (
        (local_now - timedelta(days=days - 1)).strftime("%Y-%m-%d") if days else None
    )
    range_cut = (
        (local_now - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if days
        else None
    )

    # 5h-window anchor: if autopilot ever observed a real reset time, the
    # window boundaries are reset + k*5h — far more honest than "first event in
    # the trailing 5h". One observation anchors every later window.
    anchor_source = "estimated"
    hours_cut = now - timedelta(seconds=HOUR5_SECONDS)
    if history is not None:
        reset = history.latest_reset()
        if reset is not None:
            k = (now - reset).total_seconds() // HOUR5_SECONDS
            window_start = reset + timedelta(seconds=k * HOUR5_SECONDS)
            if window_start <= now:
                hours_cut = window_start
                anchor_source = "reset"
    week_cut = now - timedelta(seconds=WEEK_SECONDS)

    by_agent: dict[str, list[float]] = {}  # agent_type -> [cost, tokens, messages]

    # --- history: days before today, within range -----------------------------
    if history is not None:
        yesterday_key = (local_now - timedelta(days=1)).strftime("%Y-%m-%d")
        for r in history.rows(range_start_day, yesterday_key):
            i, o, cc, cr = r["input"], r["output"], r["cc"], r["cr"]
            model, cost, msgs = r["model"], r["cost_usd"], r["messages"]
            p = price_for(model)
            cost_in += i * p.input / 1_000_000
            cost_out += o * p.output / 1_000_000
            cost_cw += cc * p.cache_write / 1_000_000
            cost_cr += cr * p.cache_read / 1_000_000
            cache_savings += cr * (p.input - p.cache_read) / 1_000_000
            for acc in (totals, by_model.setdefault(model, _Acc())):
                acc.input += i
                acc.output += o
                acc.cc += cc
                acc.cr += cr
                acc.cost += cost
                acc.messages += msgs
            pp = per_project.setdefault(r["project_dir"], [0.0, 0, 0, 0])
            pp[0] += cost
            pp[1] += i + o + cc + cr
            pp[2] += msgs
            ba = by_agent.setdefault(r["agent_type"], [0.0, 0, 0])
            ba[0] += cost
            ba[1] += i + o + cc + cr
            ba[2] += msgs
            if r["day"] in daily:
                daily[r["day"]][0] += i + o + cc  # work tokens, see below
                daily[r["day"]][1] += cost

    # --- live scan: windows always; totals only for today ----------------------
    for e in scan.events:
        ts = e.ts
        local_day = ts.astimezone().strftime("%Y-%m-%d") if ts else today_key
        in_range = range_cut is None or (ts is None) or ts >= range_cut.astimezone(timezone.utc)
        # Tool calls are counted regardless of whether usage is present.
        if in_range:
            for name in e.tools:
                tool_counts[name] = tool_counts.get(name, 0) + 1
        if e.total <= 0:
            continue  # tools-only event (no token usage)

        i, o, cc, cr, model = e.input, e.output, e.cc, e.cr, e.model
        cost = cost_usd(model, i, o, cc, cr)
        tok = e.total

        # Today's contribution to totals/breakdowns (history covers past days
        # when available; without a history store, all in-range days count).
        counts_in_totals = in_range and (
            history is None or local_day == today_key
        )
        if counts_in_totals:
            p = price_for(model)
            cost_in += i * p.input / 1_000_000
            cost_out += o * p.output / 1_000_000
            cost_cw += cc * p.cache_write / 1_000_000
            cost_cr += cr * p.cache_read / 1_000_000
            cache_savings += cr * (p.input - p.cache_read) / 1_000_000
            totals.add(i, o, cc, cr, cost, ts)
            by_model.setdefault(model or "unknown", _Acc()).add(i, o, cc, cr, cost, ts)
            pp = per_project.setdefault(e.project_dir, [0.0, 0, 0, 0])
            pp[0] += cost
            pp[1] += tok
            pp[2] += 1
            ba = by_agent.setdefault(e.agent_type or "", [0.0, 0, 0])
            ba[0] += cost
            ba[1] += tok
            ba[2] += 1
            if local_day in daily:
                # Work tokens only (in + out + cache writes). Cache READS are
                # re-reads of the cached prefix — including them buries the
                # signal under billions of tokens/day.
                daily[local_day][0] += i + o + cc
                daily[local_day][1] += cost
        if in_range:
            ps = per_session.setdefault(e.sid, [0.0, 0, 0])
            ps[0] += cost
            ps[1] += tok
            ps[2] += 1
        if ts:
            local_ts = ts.astimezone()
            if in_range:
                by_hour[local_ts.hour][0] += 1
                by_hour[local_ts.hour][1] += cost
            if ts >= hours_cut:
                hours.add(i, o, cc, cr, cost, ts)
                hours_events.append((ts, cost, tok))
            if ts >= week_cut:
                week.add(i, o, cc, cr, cost, ts)
                week_events.append((ts, cost, tok))

    return StatsResponse(
        generated_at=now,
        range_days=days,
        plan=plan,
        claude_cache=load_claude_stats(),
        insights=compute_insights(24, scan),
        totals=Totals(
            input_tokens=totals.input,
            output_tokens=totals.output,
            cache_creation_input_tokens=totals.cc,
            cache_read_input_tokens=totals.cr,
            total_tokens=totals.total,
            messages=totals.messages,
            sessions=sessions,
            cost_usd=round(totals.cost, 4),
        ),
        by_model=sorted(
            (
                ModelStat(
                    model=name,
                    input_tokens=a.input,
                    output_tokens=a.output,
                    cache_creation_input_tokens=a.cc,
                    cache_read_input_tokens=a.cr,
                    total_tokens=a.total,
                    messages=a.messages,
                    cost_usd=round(a.cost, 4),
                )
                for name, a in by_model.items()
            ),
            key=lambda m: m.cost_usd,
            reverse=True,
        ),
        hours=_window(
            "5-hour window", HOUR5_SECONDS, hours, now, hours_events, budget_5h,
            anchor_override=hours_cut if anchor_source == "reset" else None,
            anchor_source=anchor_source,
        ),
        week=_window("Weekly window", WEEK_SECONDS, week, now, week_events, budget_week),
        by_agent_type=sorted(
            (
                AgentTypeStat(
                    agent_type=name or "main thread",
                    cost_usd=round(v[0], 4),
                    total_tokens=int(v[1]),
                    messages=int(v[2]),
                )
                for name, v in by_agent.items()
            ),
            key=lambda a: a.cost_usd,
            reverse=True,
        ),
        daily=[
            DailyStat(date=k, total_tokens=int(v[0]), cost_usd=round(v[1], 4))
            for k, v in daily.items()
        ],
        cost_breakdown=CostBreakdown(
            input=round(cost_in, 4),
            output=round(cost_out, 4),
            cache_write=round(cost_cw, 4),
            cache_read=round(cost_cr, 4),
        ),
        cache_hit_rate=_hit_rate(totals),
        cache_savings_usd=round(cache_savings, 4),
        tools=[
            ToolCount(name=n, count=c)
            for n, c in sorted(tool_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
        ],
        top_sessions=_top_sessions(per_session, title_by_sid),
        by_project=sorted(
            (
                ProjectStat(
                    project=cwd_by_dir.get(name) or decode_cwd(name),
                    cost_usd=round(v[0], 4),
                    total_tokens=int(v[1]),
                    messages=int(v[2]),
                    sessions=int(v[3]),
                )
                for name, v in per_project.items()
            ),
            key=lambda p: p.cost_usd,
            reverse=True,
        ),
        by_hour=[
            HourStat(hour=h, messages=by_hour[h][0], cost_usd=round(by_hour[h][1], 4))
            for h in range(24)
        ],
    )


def _hit_rate(t: _Acc) -> float:
    prompt = t.input + t.cc + t.cr
    return round(t.cr / prompt, 4) if prompt else 0.0


def _top_sessions(
    per_session: dict[str, list[float]], titles: dict[str, str]
) -> list[TopSession]:
    ranked = sorted(per_session.items(), key=lambda kv: kv[1][0], reverse=True)[:8]
    return [
        TopSession(
            session_id=sid,
            title=titles.get(sid, sid[:8]),
            cost_usd=round(vals[0], 4),
            total_tokens=int(vals[1]),
            messages=int(vals[2]),
        )
        for sid, vals in ranked
    ]


def _window(
    label: str,
    window_seconds: int,
    acc: _Acc,
    now: datetime,
    events: list[tuple[datetime, float, int]],
    budget_usd: float | None,
    anchor_override: Optional[datetime] = None,
    anchor_source: str = "estimated",
) -> WindowStat:
    if anchor_override is not None:
        acc.anchor = anchor_override  # observed window boundary beats first-event
    elapsed = int((now - acc.anchor).total_seconds()) if acc.anchor else 0
    elapsed = max(0, min(window_seconds, elapsed))

    bucket_seconds = window_seconds // WINDOW_BUCKETS
    buckets = [
        Bucket(offset_seconds=b * bucket_seconds) for b in range(WINDOW_BUCKETS)
    ]
    if acc.anchor and bucket_seconds:
        for ts, cost, tok in events:
            idx = int((ts - acc.anchor).total_seconds() // bucket_seconds)
            idx = max(0, min(WINDOW_BUCKETS - 1, idx))
            buckets[idx].cost_usd += cost
            buckets[idx].total_tokens += tok
        for b in buckets:
            b.cost_usd = round(b.cost_usd, 4)

    return WindowStat(
        label=label,
        window_seconds=window_seconds,
        anchor=acc.anchor,
        anchor_source=anchor_source,
        elapsed_seconds=elapsed,
        remaining_seconds=window_seconds - elapsed,
        input_tokens=acc.input,
        output_tokens=acc.output,
        cache_tokens=acc.cc + acc.cr,
        total_tokens=acc.total,
        messages=acc.messages,
        cost_usd=round(acc.cost, 4),
        bucket_seconds=bucket_seconds,
        buckets=buckets,
        budget_usd=budget_usd,
    )
