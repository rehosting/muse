"""Replicate Claude Code's "What's contributing to your limits usage?" panel.

Computed locally from transcripts over a trailing window (default 24h). These
are *independent characteristics* of usage (they overlap; not a partition),
matching Claude Code's framing. Usage is measured in total tokens.

Consumes the shared mtime-cached event scan (usage_cache), so it adds no extra
file reads beyond what stats already does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import ContributingFactor, SubagentTypePct, UsageInsights
from .usage_cache import Scan, scan_all

HIGH_CONTEXT = 150_000
LONG_SESSION_SECONDS = 8 * 3600


class _Sess:
    def __init__(self) -> None:
        self.total = 0
        self.subagent = 0
        self.first: Optional[datetime] = None
        self.last: Optional[datetime] = None

    def mark_time(self, ts: datetime) -> None:
        if self.first is None or ts < self.first:
            self.first = ts
        if self.last is None or ts > self.last:
            self.last = ts


def compute_insights(
    window_hours: int = 24, scan: Optional[Scan] = None
) -> Optional[UsageInsights]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    if scan is None:
        scan = scan_all()

    sessions: dict[str, _Sess] = {}
    total = 0
    high_context = 0
    by_type: dict[str, int] = {}

    for e in scan.events:
        if e.total <= 0 or e.ts is None or e.ts < cutoff:
            continue
        tok = e.total
        total += tok
        s = sessions.setdefault(e.sid, _Sess())
        s.total += tok
        s.mark_time(e.ts)
        if e.is_subagent:
            s.subagent += tok
            by_type[e.agent_type] = by_type.get(e.agent_type, 0) + tok
        if e.context > HIGH_CONTEXT:
            high_context += tok

    if total <= 0:
        return UsageInsights(window_hours=window_hours, total_tokens=0, factors=[], by_subagent_type=[])

    # A session counts as subagent-heavy if it spawned subagents at all — the
    # whole session's usage is attributed (subagents drive far more requests than
    # their own token share suggests), matching Claude Code's framing.
    subagent_heavy = sum(s.total for s in sessions.values() if s.subagent > 0)
    long_session = sum(
        s.total
        for s in sessions.values()
        if s.first and s.last and (s.last - s.first).total_seconds() >= LONG_SESSION_SECONDS
    )

    def pct(n: int) -> float:
        return round(100 * n / total, 1)

    factors = [
        ContributingFactor(
            key="subagent_heavy",
            pct=pct(subagent_heavy),
            label="of your usage came from subagent-heavy sessions",
            advice="Each subagent runs its own requests. Be deliberate about spawning them — and "
            "consider a cheaper model for simpler subagents.",
        ),
        ContributingFactor(
            key="high_context",
            pct=pct(high_context),
            label="of your usage was at >150k context",
            advice="Longer sessions are more expensive even when cached. /compact mid-task, "
            "/clear when switching tasks.",
        ),
        ContributingFactor(
            key="long_session",
            pct=pct(long_session),
            label="of your usage came from sessions active for 8+ hours",
            advice="These are often background/loop sessions. Continuous usage adds up — make "
            "sure it's intentional.",
        ),
    ]
    factors = [f for f in factors if f.pct > 0]
    factors.sort(key=lambda f: f.pct, reverse=True)

    by_subagent_type = sorted(
        (SubagentTypePct(agent_type=t, pct=pct(n)) for t, n in by_type.items()),
        key=lambda s: s.pct,
        reverse=True,
    )

    return UsageInsights(
        window_hours=window_hours,
        total_tokens=total,
        factors=factors,
        by_subagent_type=by_subagent_type,
    )
