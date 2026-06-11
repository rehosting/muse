"""Assemble normalized Threads from on-disk transcripts.

This is the IO + assembly layer: read JSONL, parse lines, pair tool results to
tool uses (second pass), derive a title, and attach subagent references.
"""

from __future__ import annotations

import orjson
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import (
    SubagentRef,
    Thread,
    ThreadItem,
    ToolResult,
    Usage,
)
from .parser import extract_tool_results, parse_line
from .paths import SessionPaths, decode_cwd, find_session


def iter_json_lines(path: Path) -> Iterator[dict[str, Any]]:
    """Yield decoded JSON objects from a JSONL file, skipping bad/partial lines."""
    if not path.is_file():
        return
    with path.open("rb") as fh:  # bytes + orjson is the fast path
        for line in fh:
            if not line.strip():
                continue
            try:
                yield orjson.loads(line)
            except orjson.JSONDecodeError:
                continue


def _derive_title(raw_lines: list[dict[str, Any]], items: list[ThreadItem]) -> tuple[str, str]:
    """Return (title, source). Precedence: last ai-title > first user text > slug."""
    ai_title: Optional[str] = None
    slug: Optional[str] = None
    for obj in raw_lines:
        if obj.get("type") == "ai-title" and obj.get("aiTitle"):
            ai_title = obj["aiTitle"]  # keep the last one seen
        if slug is None and obj.get("slug"):
            slug = obj["slug"]
    if ai_title:
        return ai_title, "ai-title"
    for item in items:
        if item.role == "user" and item.text:
            first = item.text.strip().splitlines()[0] if item.text.strip() else ""
            if first:
                return (first[:120], "user")
    if slug:
        return slug.replace("-", " "), "slug"
    return "Untitled session", "none"


def _pair_and_total(
    items: list[ThreadItem], results: dict[str, ToolResult]
) -> Usage:
    """Attach tool results to their tool_uses and sum assistant usage."""
    total = Usage()
    for item in items:
        if item.usage:
            total = total.add(item.usage)
        for block in item.blocks:
            tu = block.tool_use
            if tu and tu.id in results:
                tu.result = results[tu.id]
    return total


def _load_subagent_refs(paths: SessionPaths) -> dict[str, SubagentRef]:
    """Map parent tool_use_id -> SubagentRef from subagents/*.meta.json."""
    refs: dict[str, SubagentRef] = {}
    sub_dir = paths.subagents_dir
    if not sub_dir.is_dir():
        return refs
    for meta_file in sub_dir.glob("*.meta.json"):
        try:
            meta = orjson.loads(meta_file.read_bytes())
        except (orjson.JSONDecodeError, OSError):
            continue
        # filename: agent-{id}.meta.json
        agent_id = meta_file.name[: -len(".meta.json")]
        tool_use_id = meta.get("toolUseId")
        if not tool_use_id:
            continue
        refs[tool_use_id] = SubagentRef(
            agent_id=agent_id,
            agent_type=meta.get("agentType", "agent"),
            description=meta.get("description", ""),
            tool_use_id=tool_use_id,
        )
    return refs


def _attach_subagents(items: list[ThreadItem], refs: dict[str, SubagentRef]) -> None:
    if not refs:
        return
    for item in items:
        for block in item.blocks:
            tu = block.tool_use
            if tu and tu.id in refs:
                tu.subagent = refs[tu.id]


def _build_thread_from_lines(
    raw_lines: list[dict[str, Any]],
    session_id: str,
) -> tuple[list[ThreadItem], dict[str, ToolResult], Usage, str, str, Optional[str]]:
    items: list[ThreadItem] = []
    results: dict[str, ToolResult] = {}
    project_cwd: Optional[str] = None
    for obj in raw_lines:
        if project_cwd is None and obj.get("cwd"):
            project_cwd = obj["cwd"]
        for res in extract_tool_results(obj):
            if res.tool_use_id:
                results[res.tool_use_id] = res
        item = parse_line(obj)
        if item is not None:
            items.append(item)
    total = _pair_and_total(items, results)
    title, source = _derive_title(raw_lines, items)
    return items, results, total, title, source, project_cwd


def load_thread(session_id: str) -> Optional[Thread]:
    paths = find_session(session_id)
    if paths is None:
        return None
    raw_lines = list(iter_json_lines(paths.jsonl))
    items, _results, total, title, source, project_cwd = _build_thread_from_lines(
        raw_lines, session_id
    )
    if project_cwd is None:
        project_cwd = decode_cwd(paths.project_dir)
    version = next((o.get("version") for o in raw_lines if o.get("version")), None)
    _attach_subagents(items, _load_subagent_refs(paths))
    return Thread(
        session_id=session_id,
        project_cwd=project_cwd,
        version=version,
        title=title,
        title_source=source,  # type: ignore[arg-type]
        items=items,
        usage_total=total,
    )


def load_subagent_thread(session_id: str, agent_id: str) -> Optional[Thread]:
    paths = find_session(session_id)
    if paths is None:
        return None
    jsonl = paths.subagent_jsonl(agent_id)
    if not jsonl.is_file():
        return None
    raw_lines = list(iter_json_lines(jsonl))
    items, _results, total, title, source, project_cwd = _build_thread_from_lines(
        raw_lines, session_id
    )
    # Subagents can themselves spawn subagents — attach nested refs too.
    _attach_subagents(items, _load_subagent_refs(paths))

    # Read this subagent's own meta for type/description and the parent link.
    meta = _read_meta(paths, agent_id)
    return Thread(
        session_id=session_id,
        project_cwd=project_cwd or decode_cwd(paths.project_dir),
        title=meta.get("description") or title,
        title_source=source,  # type: ignore[arg-type]
        items=items,
        usage_total=total,
        agent_id=agent_id if agent_id.startswith("agent-") else f"agent-{agent_id}",
        agent_type=meta.get("agentType"),
        description=meta.get("description"),
        parent_tool_use_id=meta.get("toolUseId"),
    )


def _read_meta(paths: SessionPaths, agent_id: str) -> dict[str, Any]:
    meta_file = paths.subagent_meta(agent_id)
    if not meta_file.is_file():
        return {}
    try:
        return orjson.loads(meta_file.read_bytes())
    except (orjson.JSONDecodeError, OSError):
        return {}
