"""Read Claude Code's own usage rollup (~/.claude/stats-cache.json), read-only.

This is authoritative — Claude Code computes it itself — so we surface it as a
cross-check against muse's transcript-derived numbers. The file stores token
counts but no cost, so we price it with the same pricing table muse uses.
"""

from __future__ import annotations

import json

from .config import get_settings
from .models import ClaudeCacheStats, ClaudeDaily, ClaudeModelUsage
from .pricing import cost_usd


def load_claude_stats() -> ClaudeCacheStats | None:
    path = get_settings().claude_dir / "stats-cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    by_model: list[ClaudeModelUsage] = []
    total_tokens = 0
    total_cost = 0.0
    for model, u in (data.get("modelUsage") or {}).items():
        i = u.get("inputTokens", 0) or 0
        o = u.get("outputTokens", 0) or 0
        cr = u.get("cacheReadInputTokens", 0) or 0
        cc = u.get("cacheCreationInputTokens", 0) or 0
        total = i + o + cr + cc
        cost = cost_usd(model, i, o, cc, cr)
        total_tokens += total
        total_cost += cost
        by_model.append(
            ClaudeModelUsage(
                model=model,
                input_tokens=i,
                output_tokens=o,
                cache_read_input_tokens=cr,
                cache_creation_input_tokens=cc,
                total_tokens=total,
                cost_usd=round(cost, 4),
            )
        )
    by_model.sort(key=lambda m: m.cost_usd, reverse=True)

    # Merge dailyActivity (messages/tool calls/sessions) with dailyModelTokens.
    activity = {d.get("date"): d for d in (data.get("dailyActivity") or [])}
    daily: list[ClaudeDaily] = []
    for d in data.get("dailyModelTokens") or []:
        date = d.get("date")
        toks = sum((d.get("tokensByModel") or {}).values())
        a = activity.get(date, {})
        daily.append(
            ClaudeDaily(
                date=date,
                total_tokens=toks,
                messages=a.get("messageCount", 0),
                tool_calls=a.get("toolCallCount", 0),
                sessions=a.get("sessionCount", 0),
            )
        )

    total_tool_calls = sum(a.get("toolCallCount", 0) for a in (data.get("dailyActivity") or []))

    return ClaudeCacheStats(
        last_computed_date=data.get("lastComputedDate"),
        total_sessions=data.get("totalSessions", 0),
        total_messages=data.get("totalMessages", 0),
        total_tool_calls=total_tool_calls,
        total_tokens=total_tokens,
        cost_usd=round(total_cost, 4),
        by_model=by_model,
        daily=daily,
    )
