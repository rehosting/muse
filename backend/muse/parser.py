"""Turn raw JSONL lines into normalized ThreadItems.

Design goals:
- Tolerant: switch on known `type`s; unknown/irrelevant lines return None and
  never raise. New Claude Code versions add line types over time.
- Pure: no filesystem access here. transcript.py orchestrates IO and pairing.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import ContentBlock, ThreadItem, ToolResult, ToolUse, Usage
from .persisted import detect_persisted

# Line types we deliberately skip (metadata / bookkeeping, not conversation).
_SKIP_TYPES = {
    "mode",
    "permission-mode",
    "ai-title",
    "file-history-snapshot",
    "attachment",
    "last-prompt",
    "queue-operation",
    "agent-name",
    "pr-link",
    "summary",
}


def parse_usage(raw: Optional[dict[str, Any]]) -> Optional[Usage]:
    if not isinstance(raw, dict):
        return None
    return Usage(
        input_tokens=raw.get("input_tokens", 0) or 0,
        output_tokens=raw.get("output_tokens", 0) or 0,
        cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=raw.get("cache_read_input_tokens", 0) or 0,
        service_tier=raw.get("service_tier"),
    )


def _stringify_result_content(content: Any) -> str:
    """tool_result.content is a string OR a list of blocks; flatten to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    # Non-text blocks (e.g. images) — note their presence.
                    parts.append(f"[{block.get('type', 'block')}]")
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return "" if content is None else str(content)


def parse_tool_result(block: dict[str, Any]) -> ToolResult:
    text = _stringify_result_content(block.get("content"))
    result = ToolResult(
        tool_use_id=block.get("tool_use_id", ""),
        is_error=bool(block.get("is_error", False)),
        content=text,
    )
    persisted = detect_persisted(text)
    if persisted:
        result.truncated = True
        result.cache_id = persisted.cache_id
        result.preview = persisted.preview
        result.content = None  # don't ship the wrapper; preview carries the gist
    return result


def _parse_assistant_blocks(content: Any) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    if not isinstance(content, list):
        if isinstance(content, str) and content:
            blocks.append(ContentBlock(kind="text", text=content))
        return blocks
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            blocks.append(ContentBlock(kind="text", text=block.get("text", "")))
        elif btype == "thinking":
            blocks.append(
                ContentBlock(kind="thinking", text=block.get("thinking", block.get("text", "")))
            )
        elif btype == "tool_use":
            blocks.append(
                ContentBlock(
                    kind="tool_use",
                    tool_use=ToolUse(
                        id=block.get("id", ""),
                        name=block.get("name", "unknown"),
                        input=block.get("input", {}) or {},
                        caller=block.get("caller"),
                    ),
                )
            )
    return blocks


def parse_line(obj: dict[str, Any]) -> Optional[ThreadItem]:
    """Parse one decoded JSONL object into a ThreadItem, or None to skip it.

    Note: tool_result blocks embedded in user lines are NOT attached here; the
    transcript layer collects them in a second pass and pairs them to tool_uses.
    """
    ltype = obj.get("type")
    if ltype in _SKIP_TYPES:
        return None

    if ltype == "assistant":
        msg = obj.get("message", {}) or {}
        return ThreadItem(
            uuid=obj.get("uuid", ""),
            parent_uuid=obj.get("parentUuid"),
            role="assistant",
            type="assistant",
            timestamp=obj.get("timestamp"),
            blocks=_parse_assistant_blocks(msg.get("content")),
            usage=parse_usage(msg.get("usage")),
            model=msg.get("model"),
            is_sidechain=bool(obj.get("isSidechain", False)),
        )

    if ltype == "user":
        msg = obj.get("message", {}) or {}
        content = msg.get("content")
        text: Optional[str] = None
        blocks: list[ContentBlock] = []
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            texts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                text = "\n".join(t for t in texts if t)
        return ThreadItem(
            uuid=obj.get("uuid", ""),
            parent_uuid=obj.get("parentUuid"),
            role="user",
            type="user",
            timestamp=obj.get("timestamp"),
            text=text,
            blocks=blocks,
            is_sidechain=bool(obj.get("isSidechain", False)),
        )

    if ltype == "system":
        return ThreadItem(
            uuid=obj.get("uuid", ""),
            parent_uuid=obj.get("parentUuid"),
            role="system",
            type="system",
            timestamp=obj.get("timestamp"),
            text=obj.get("content"),
            level=obj.get("level"),
            is_sidechain=bool(obj.get("isSidechain", False)),
        )

    return None


def extract_tool_results(obj: dict[str, Any]) -> list[ToolResult]:
    """Pull any tool_result blocks out of a user line for second-pass pairing."""
    if obj.get("type") != "user":
        return []
    content = obj.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    results: list[ToolResult] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            results.append(parse_tool_result(block))
    return results
