"""Session lineage: segment a session by its compaction boundaries.

Resuming a session appends to the same transcript (same sessionId), so a
session's internal "lineage" is the set of points where Claude Code compacted
the context — each `system`/`compact_boundary` line carries `compactMetadata`
(trigger, preTokens, durationMs). A session with K boundaries has K+1 segments.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .models import CompactionBoundary, SessionLineage
from .transcript import iter_json_lines


def _ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_lineage(jsonl_path, session_id: str) -> SessionLineage:
    boundaries: list[CompactionBoundary] = []
    total_pre = 0
    for obj in iter_json_lines(jsonl_path):
        if obj.get("type") != "system" or obj.get("subtype") != "compact_boundary":
            continue
        meta = obj.get("compactMetadata") or {}
        pre = meta.get("preTokens") if isinstance(meta.get("preTokens"), int) else None
        dur = meta.get("durationMs")
        if pre:
            total_pre += pre
        boundaries.append(
            CompactionBoundary(
                uuid=obj.get("uuid"),
                timestamp=_ts(obj.get("timestamp")),
                trigger=meta.get("trigger"),
                pre_tokens=pre,
                duration_ms=int(dur) if isinstance(dur, (int, float)) else None,
            )
        )
    return SessionLineage(
        session_id=session_id,
        segment_count=len(boundaries) + 1,
        total_pre_tokens=total_pre,
        boundaries=boundaries,
    )
