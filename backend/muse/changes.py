"""Aggregate per-file activity across a reconstructed thread.

Answers "what files did this session touch, and how did they change?" by
bucketing the file-touching tool calls (Read/Edit/MultiEdit/Write/NotebookEdit)
by path. We return only the tool_use_ids — the frontend already holds every
ToolUse and renders diffs via the existing renderers, so we never duplicate the
old_string/new_string payload here.
"""

from __future__ import annotations

from typing import Optional

from .models import FileChange, FileOp, FileOpKind, Thread, ToolUse

# tool name -> the op kind it represents.
_KIND: dict[str, FileOpKind] = {
    "Read": "read",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Write": "write",
    "NotebookEdit": "edit",
}


def _file_path(tool: ToolUse) -> Optional[str]:
    inp = tool.input or {}
    path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
    return str(path) if path else None


def _edit_count(tool: ToolUse) -> int:
    edits = (tool.input or {}).get("edits")
    return len(edits) if isinstance(edits, list) and edits else 1


def build_file_changes(thread: Thread) -> list[FileChange]:
    by_path: dict[str, FileChange] = {}

    for item in thread.items:
        for block in item.blocks:
            tool = block.tool_use
            if tool is None:
                continue
            kind = _KIND.get(tool.name)
            if kind is None:
                continue
            path = _file_path(tool)
            if not path:
                continue

            fc = by_path.get(path)
            if fc is None:
                fc = FileChange(path=path)
                by_path[path] = fc

            is_error = bool(tool.result and tool.result.is_error)
            ec = _edit_count(tool) if kind == "edit" else 1
            fc.ops.append(
                FileOp(
                    tool_use_id=tool.id,
                    kind=kind,
                    tool_name=tool.name,
                    timestamp=item.timestamp,
                    is_error=is_error,
                    edit_count=ec,
                )
            )
            if kind == "read":
                fc.read_count += 1
            elif kind == "edit":
                fc.edit_count += ec
            else:
                fc.write_count += 1
            if is_error:
                fc.error_count += 1
            if item.timestamp:
                if fc.first_ts is None or item.timestamp < fc.first_ts:
                    fc.first_ts = item.timestamp
                if fc.last_ts is None or item.timestamp > fc.last_ts:
                    fc.last_ts = item.timestamp

    def sort_key(fc: FileChange) -> tuple[int, float]:
        # Most-touched first; tie-break on most-recently-touched. Use epoch
        # seconds so naive/aware timestamps never get compared directly.
        last = fc.last_ts.timestamp() if fc.last_ts else 0.0
        return (len(fc.ops), last)

    return sorted(by_path.values(), key=sort_key, reverse=True)
