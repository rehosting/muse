"""Outcome-aware analytics: what each session COST vs. what it PRODUCED.

Pure compute over inputs the alerts tick already keeps warm (usage rollup,
file-activity windows, health snapshots, git provenance, usage_daily history) —
no transcript parsing on the request path. Provenance is evidence-based, never
authorship proof, so only HIGH+MEDIUM confidence commits count as "shipped";
LOW is surfaced separately and never enters a ratio.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Optional

from .models import (
    HeatDay,
    MatrixCell,
    OutcomeRatio,
    OutcomesResponse,
    SessionOutcome,
)
from .paths import decode_cwd

_COST_FLOOR = 0.5  # a $0.02 one-commit session shouldn't dominate the ranking
_RATIO_MIN_COST = 1.0  # don't print a productivity ratio from noise


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local_cell(iso: str) -> Optional[tuple[int, int]]:
    dt = _parse(iso)
    if dt is None:
        return None
    local = dt.astimezone()
    return local.weekday(), local.hour


def compute_outcomes(
    *,
    days: int,
    now: datetime,
    summaries: list,
    rollup: dict[str, tuple[int, float]],
    windows: dict[str, tuple[Optional[str], Optional[str]]],
    health_rows: dict[str, dict],
    commits_by_sid: dict[str, list[dict]],
    history_rows: list,
    error_times: list[str],
    commit_times: list[str],
    scan,
) -> OutcomesResponse:
    outcomes: list[SessionOutcome] = []
    for s in summaries:
        tokens, cost = rollup.get(s.session_id, (0, 0.0))
        first_raw, last_raw = windows.get(s.session_id, (None, None))
        started = _parse(first_raw)
        ended = max(filter(None, [_parse(last_raw), s.mtime]), default=None)
        if started is None:
            started = ended  # no file activity → point window at mtime
        duration = (ended - started).total_seconds() if started and ended else 0.0

        commits = commits_by_sid.get(s.session_id, [])
        hi = [c for c in commits if c["confidence"] == "high"]
        med = [c for c in commits if c["confidence"] == "medium"]
        low = [c for c in commits if c["confidence"] == "low"]
        subjects = [c["subject"] for c in sorted(
            hi + med, key=lambda c: c.get("committer_date") or "", reverse=True
        )][:3]

        health = health_rows.get(s.session_id, {})
        outcomes.append(SessionOutcome(
            session_id=s.session_id,
            title=s.title,
            project_cwd=s.project_cwd,
            provider=s.provider,
            model=s.model,
            cost_usd=round(cost, 4),
            work_tokens=tokens,
            started=started,
            ended=ended,
            duration_seconds=max(0.0, duration),
            commits_high=len(hi),
            commits_medium=len(med),
            commits_low=len(low),
            commit_subjects=[t for t in subjects if t],
            health=health.get("score") or s.health,
            error_count=health.get("error_count", 0),
        ))

    # --- shipped vs burned ----------------------------------------------------
    def shipped(o: SessionOutcome) -> int:
        return o.commits_high + o.commits_medium

    costs = [o.cost_usd for o in outcomes if o.cost_usd > 0]
    cost_median = median(costs) if costs else 0.0

    most_productive = sorted(
        [o for o in outcomes if shipped(o) >= 1],
        key=lambda o: shipped(o) / max(o.cost_usd, _COST_FLOOR),
        reverse=True,
    )[:10]
    most_wasteful = sorted(
        [o for o in outcomes
         if shipped(o) == 0 and (o.health not in (None, "ok") or o.cost_usd > cost_median)],
        key=lambda o: o.cost_usd,
        reverse=True,
    )[:10]

    by_project = _ratios(outcomes, lambda o: o.project_cwd or "(unknown)")
    by_model = _ratios(outcomes, lambda o: o.model or "(unknown)")

    # --- calendar heatmap (durable usage_daily; ≥180 days) --------------------
    cal: dict[str, HeatDay] = {}
    for r in history_rows:
        d = r["day"]
        cell = cal.setdefault(d, HeatDay(day=d))
        cell.cost_usd += r["cost_usd"]
        cell.work_tokens += r["input"] + r["output"] + r["cc"]
    calendar = sorted(cal.values(), key=lambda h: h.day)

    # --- hour × weekday matrix (local time) -----------------------------------
    cells: dict[tuple[int, int], MatrixCell] = {
        (d, h): MatrixCell(dow=d, hour=h) for d in range(7) for h in range(24)
    }
    for e in scan.events:
        if e.is_subagent or e.ts is None:
            continue
        c = _local_cell(e.ts.isoformat() if hasattr(e.ts, "isoformat") else str(e.ts))
        if c:
            cells[c].activity += 1
            cells[c].cost_usd += 0.0  # activity layer; cost shown via calendar
    for ts in error_times:
        c = _local_cell(ts)
        if c:
            cells[c].errors += 1
    for ts in commit_times:
        c = _local_cell(ts)
        if c:
            cells[c].commits += 1

    return OutcomesResponse(
        generated_at=now,
        range_days=days,
        outcomes=sorted(outcomes, key=lambda o: o.cost_usd, reverse=True),
        most_productive=most_productive,
        most_wasteful=most_wasteful,
        by_project=by_project,
        by_model=by_model,
        calendar=calendar,
        matrix=list(cells.values()),
        notes=[
            "Commits are linked by evidence (time window + file overlap + branch), "
            "not authorship proof; only high+medium confidence count as shipped.",
            "The activity layer of the matrix reflects retained transcripts only; "
            "errors and commits are durable.",
        ],
    )


def _ratios(outcomes: list[SessionOutcome], key_fn) -> list[OutcomeRatio]:
    agg: dict[str, OutcomeRatio] = {}
    for o in outcomes:
        k = key_fn(o)
        r = agg.get(k)
        if r is None:
            r = agg[k] = OutcomeRatio(key=k)
        r.cost_usd += o.cost_usd
        r.commits += o.commits_high + o.commits_medium
        r.commits_low += o.commits_low
        r.sessions += 1
    for r in agg.values():
        r.cost_usd = round(r.cost_usd, 2)
        if r.cost_usd >= _RATIO_MIN_COST:
            r.commits_per_10usd = round(10.0 * r.commits / r.cost_usd, 2)
    # Shorten project keys to a readable tail for display callers if they want.
    return sorted(agg.values(), key=lambda r: r.commits, reverse=True)


def project_label(cwd_or_dir: str) -> str:
    """Human label for a project key (decode an encoded dir if needed)."""
    if "/" in cwd_or_dir:
        return cwd_or_dir
    return decode_cwd(cwd_or_dir)
