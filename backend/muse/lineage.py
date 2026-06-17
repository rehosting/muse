"""Session lineage: segment a session by its compaction boundaries.

Resuming a session appends to the same transcript (same sessionId), so a
session's internal "lineage" is the set of points where Claude Code compacted
the context — each `system`/`compact_boundary` line carries `compactMetadata`
(trigger, preTokens, durationMs). A session with K boundaries has K+1 segments.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .incremental import new_objects
from .models import CompactionBoundary, SessionLineage


def _ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# path -> (mtime, offset, boundaries, total_pre). Compaction boundaries are rare
# (0-3 per session) but scanning the whole transcript to find them cost ~0.23s on
# every viewer open. The transcript is append-only, so cache by mtime and only
# parse bytes appended since the last call (live sessions stay cheap too).
_cache: dict[Path, tuple[float, int, list[CompactionBoundary], int]] = {}


def _boundary(obj: dict) -> Optional[CompactionBoundary]:
    if obj.get("type") != "system" or obj.get("subtype") != "compact_boundary":
        return None
    meta = obj.get("compactMetadata") or {}
    pre = meta.get("preTokens") if isinstance(meta.get("preTokens"), int) else None
    dur = meta.get("durationMs")
    return CompactionBoundary(
        uuid=obj.get("uuid"),
        timestamp=_ts(obj.get("timestamp")),
        trigger=meta.get("trigger"),
        pre_tokens=pre,
        duration_ms=int(dur) if isinstance(dur, (int, float)) else None,
    )


def build_lineage(jsonl_path, session_id: str) -> SessionLineage:
    path = Path(jsonl_path)
    try:
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0.0, 0
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        boundaries, total_pre = cached[2], cached[3]
    else:
        # Resume from the cached offset if the file only grew (append); a shrink
        # (rewrite) means we can't trust the cached state, so re-read from 0.
        resume = cached is not None and size >= cached[1]
        base_offset = cached[1] if resume else 0
        boundaries = list(cached[2]) if resume else []
        total_pre = cached[3] if resume else 0
        objs, new_offset = new_objects(path, base_offset)
        for obj in objs:
            b = _boundary(obj)
            if b is None:
                continue
            if b.pre_tokens:
                total_pre += b.pre_tokens
            boundaries.append(b)
        _cache[path] = (mtime, new_offset, boundaries, total_pre)
    return SessionLineage(
        session_id=session_id,
        segment_count=len(boundaries) + 1,
        total_pre_tokens=total_pre,
        boundaries=boundaries,
    )
