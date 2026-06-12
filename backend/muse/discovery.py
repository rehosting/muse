"""Scan ~/.claude/projects and summarize every session.

Cheap pass: we read each transcript line-by-line but only pull what the list
page needs (title, counts, model, branch, cwd). Results are cached by
(path, mtime) so repeated scans are nearly free until a file changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import get_settings
from .incremental import new_objects
from .models import SessionSummary
from .paths import decode_cwd


@dataclass
class _Acc:
    """Incrementally-built summary state for one transcript (append-only)."""

    message_count: int = 0
    model: Optional[str] = None
    branch: Optional[str] = None
    cwd: Optional[str] = None
    slug: Optional[str] = None
    ai_title: Optional[str] = None
    first_user_text: Optional[str] = None
    last_msg_role: Optional[str] = None
    last_assistant_stop: Optional[str] = None


# path -> (mtime, byte offset, accumulator) for incremental re-scans
_cache: dict[Path, tuple[float, int, _Acc]] = {}


def _accumulate(acc: _Acc, obj: dict[str, Any]) -> None:
    ltype = obj.get("type")
    if acc.cwd is None and obj.get("cwd"):
        acc.cwd = obj["cwd"]
    if acc.branch is None and obj.get("gitBranch"):
        acc.branch = obj["gitBranch"]
    if acc.slug is None and obj.get("slug"):
        acc.slug = obj["slug"]
    if ltype == "ai-title" and obj.get("aiTitle"):
        acc.ai_title = obj["aiTitle"]
    elif ltype == "assistant":
        acc.message_count += 1
        msg = obj.get("message", {})
        if msg.get("model"):
            acc.model = msg["model"]
        acc.last_msg_role = "assistant"
        acc.last_assistant_stop = msg.get("stop_reason")
    elif ltype == "user":
        acc.message_count += 1
        acc.last_msg_role = "user"
        if acc.first_user_text is None:
            acc.first_user_text = _first_user_text(obj)


def _scan_file(jsonl: Path, project_dir: str) -> Optional[SessionSummary]:
    try:
        stat = jsonl.stat()
    except OSError:
        return None
    mtime, size = stat.st_mtime, stat.st_size

    cached = _cache.get(jsonl)
    if cached and cached[0] == mtime:
        acc = cached[2]
    else:
        # Resume from the cached offset if the file only grew; else full re-scan.
        # Copy the cached acc so concurrent scans don't double-count into it.
        base_offset = cached[1] if (cached and size >= cached[1]) else 0
        acc = replace(cached[2]) if (cached and base_offset > 0) else _Acc()
        objs, new_offset = new_objects(jsonl, base_offset)
        for obj in objs:
            _accumulate(acc, obj)
        _cache[jsonl] = (mtime, new_offset, acc)

    title, source = _pick_title(acc.ai_title, acc.first_user_text, acc.slug)
    # "Waiting" = Claude produced a final turn (end_turn) and is awaiting the user.
    awaiting_user = acc.last_msg_role == "assistant" and acc.last_assistant_stop == "end_turn"

    summary = SessionSummary(
        session_id=jsonl.stem,
        project_cwd=acc.cwd or decode_cwd(project_dir),
        project_dir=project_dir,
        title=title,
        title_source=source,
        message_count=acc.message_count,
        model=acc.model,
        git_branch=acc.branch,
        mtime=datetime.fromtimestamp(mtime, tz=timezone.utc),
        size_bytes=size,
        subagent_count=_count_subagents(jsonl, jsonl.stem),
        awaiting_user=awaiting_user,
    )
    _apply_state(summary, mtime)
    return summary


def apply_state(summary: SessionSummary, mtime: float) -> None:
    """Public re-application of the time-dependent state fields. The board
    ticker calls this every tick on (copies of) cached summaries so live→waiting
    flips show within seconds instead of waiting out the list cache TTL."""
    _apply_state(summary, mtime)


def _first_user_text(obj: dict[str, Any]) -> Optional[str]:
    content = obj.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text")
    return None


def _pick_title(
    ai_title: Optional[str], first_user_text: Optional[str], slug: Optional[str]
) -> tuple[str, str]:
    if ai_title:
        return ai_title, "ai-title"
    if first_user_text and first_user_text.strip():
        return first_user_text.strip().splitlines()[0][:120], "user"
    if slug:
        return slug.replace("-", " "), "slug"
    return "Untitled session", "none"


def _count_subagents(jsonl: Path, session_id: str) -> int:
    sub_dir = jsonl.parent / session_id / "subagents"
    if not sub_dir.is_dir():
        return 0
    return sum(1 for _ in sub_dir.glob("*.meta.json"))


def _is_running(mtime: float) -> bool:
    return (time.time() - mtime) <= get_settings().running_threshold_seconds


def _apply_state(summary: SessionSummary, mtime: float) -> None:
    """Set time-dependent fields (is_running, state) relative to 'now'.

    - stopped: idle beyond the stopped threshold (abandoned), either case.
    - waiting: Claude produced a final turn and is awaiting the user.
    - live:    anything else — i.e. mid-task (the model's turn). This stays live
               through long tool runs / generations that don't flush for a while,
               so an actively-running session isn't mislabelled "stopped".
    """
    settings = get_settings()
    idle = time.time() - mtime
    if idle > settings.stopped_threshold_seconds:
        summary.state = "stopped"
    elif summary.awaiting_user:
        summary.state = "waiting"
    else:
        summary.state = "live"
    summary.is_running = summary.state == "live"


def list_sessions() -> list[SessionSummary]:
    projects = get_settings().projects_dir
    if not projects.is_dir():
        return []
    summaries: list[SessionSummary] = []
    for project_dir in projects.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                summary = _scan_file(jsonl, project_dir.name)
            except OSError:
                continue
            if summary:
                summaries.append(summary)
    summaries.sort(key=lambda s: s.mtime, reverse=True)
    return summaries
