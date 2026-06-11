"""Build a complete, low-level event timeline for a session.

Unlike the conversation Thread (which keeps only user/assistant/system items),
this walks the *raw* JSONL and emits one event per meaningful entry of every
type — user prompts, assistant text/thinking, tool calls and their results,
subagent spawns, system messages (incl. turn_duration), and the lifecycle types
(permission-mode, mode, ai-title, queue-operation, pr-link, attachment, …).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .models import SessionEvent
from .parser import _stringify_result_content  # flatten tool_result content
from .persisted import detect_persisted
from .transcript import _load_subagent_refs, iter_json_lines
from .paths import SessionPaths


def _ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tool_summary(name: str, inp: dict) -> str:
    for key in ("command", "file_path", "pattern", "query", "url", "description"):
        if inp.get(key):
            return str(inp[key])
    if name in ("TodoWrite",) and isinstance(inp.get("todos"), list):
        return f"{len(inp['todos'])} todos"
    vals = list(inp.values())
    return str(vals[0])[:120] if vals else ""


def _first_line(text: str, n: int = 100) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0]
    return first[:n] + ("…" if len(first) > n or "\n" in text else "")


def _result_brief(text: str, truncated: bool) -> str:
    if truncated:
        return "large output (truncated)"
    if not text:
        return "(no output)"
    lines = text.split("\n")
    return f"{len(lines)} lines" if len(lines) > 1 else text[:100]


def build_events(jsonl_path, paths: SessionPaths) -> list[SessionEvent]:
    raw = list(iter_json_lines(jsonl_path))
    sub_refs = _load_subagent_refs(paths)

    # Pre-scan: pair tool results to calls (results arrive in a later user line).
    results: dict[str, dict] = {}
    for obj in raw:
        if obj.get("type") != "user":
            continue
        content = obj.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                if not tid:
                    continue
                text = _stringify_result_content(b.get("content"))
                persisted = detect_persisted(text)
                results[tid] = {
                    "is_error": bool(b.get("is_error")),
                    "truncated": persisted is not None,
                    "ts": _ts(obj.get("timestamp")),
                    "brief": _result_brief(
                        persisted.preview if persisted else text, persisted is not None
                    ),
                }

    events: list[SessionEvent] = []
    n = 0
    # These are restated every turn even when unchanged — emit only on change.
    dedup_field = {"mode": "mode", "permission-mode": "permissionMode", "ai-title": "aiTitle"}
    last_value: dict[str, Any] = {}

    def add(**kw) -> None:
        nonlocal n
        events.append(SessionEvent(index=n, **kw))
        n += 1

    for obj in raw:
        ltype = obj.get("type")
        uuid = obj.get("uuid")
        ts = _ts(obj.get("timestamp"))

        if ltype == "assistant":
            msg = obj.get("message", {}) or {}
            for block in msg.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text" and block.get("text", "").strip():
                    add(kind="assistant_text", type="assistant", role="assistant",
                        timestamp=ts, anchor_uuid=uuid, label=_first_line(block["text"]),
                        detail=block["text"])
                elif bt == "thinking":
                    think = block.get("thinking", block.get("text", ""))
                    add(kind="thinking", type="assistant", role="assistant", timestamp=ts,
                        anchor_uuid=uuid, label="thinking", detail=think)
                elif bt == "tool_use":
                    tid = block.get("id", "")
                    name = block.get("name", "tool")
                    inp = block.get("input", {}) or {}
                    res = results.get(tid)
                    dur = (
                        int((res["ts"] - ts).total_seconds() * 1000)
                        if res and res.get("ts") and ts
                        else None
                    )
                    sub = sub_refs.get(tid)
                    if sub:
                        add(kind="subagent", type="assistant", role="assistant", timestamp=ts,
                            anchor_uuid=uuid, tool_use_id=tid, tool_name=name,
                            label=f"{sub.agent_type}: {sub.description}"[:120],
                            subagent=sub, duration_ms=dur)
                    else:
                        add(kind="tool_call", type="assistant", role="assistant", timestamp=ts,
                            anchor_uuid=uuid, tool_use_id=tid, tool_name=name,
                            label=f"{name}({_tool_summary(name, inp)})"[:140], duration_ms=dur)

        elif ltype == "user":
            content = obj.get("message", {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tid = b.get("tool_use_id", "")
                        r = results.get(tid, {})
                        status = (
                            "error" if r.get("is_error")
                            else "truncated" if r.get("truncated")
                            else "ok"
                        )
                        add(kind="tool_result", type="user", role="user", timestamp=ts,
                            anchor_uuid=uuid, tool_use_id=tid, status=status,
                            is_error=bool(r.get("is_error")), label=r.get("brief", ""))
            else:
                text = content if isinstance(content, str) else ""
                if text.strip():
                    add(kind="user", type="user", role="user", timestamp=ts,
                        anchor_uuid=uuid, label=_first_line(text), detail=text)

        elif ltype == "system":
            subtype = obj.get("subtype") or "system"
            level = obj.get("level")
            dur = obj.get("durationMs")
            content = str(obj.get("content") or "")
            if subtype == "compact_boundary":
                meta = obj.get("compactMetadata") or {}
                pre = meta.get("preTokens")
                trigger = meta.get("trigger")
                cdur = meta.get("durationMs")
                bits = ["Context compacted"]
                if isinstance(pre, int):
                    bits.append(f"{round(pre / 1000)}k tokens")
                if trigger:
                    bits.append(str(trigger))
                add(kind="system", type="compact_boundary", role="system", timestamp=ts,
                    anchor_uuid=uuid, label=" · ".join(bits), is_compaction=True,
                    detail=_compaction_detail(meta), level=level,
                    duration_ms=int(cdur) if isinstance(cdur, (int, float)) else None)
                continue
            if subtype == "turn_duration" and dur:
                label = f"turn complete · {round(dur / 1000)}s"
            else:
                label = (content or subtype)[:120]
            add(kind="system", type="system", role="system", timestamp=ts, anchor_uuid=uuid,
                label=label, detail=content or label, level=level, is_error=(level == "error"),
                duration_ms=int(dur) if isinstance(dur, (int, float)) else None)

        else:
            if ltype in dedup_field:
                val = obj.get(dedup_field[ltype])
                if last_value.get(ltype) == val:
                    continue  # unchanged restatement — skip
                last_value[ltype] = val
            label = _lifecycle_label(ltype, obj)
            if label is not None:
                add(kind="lifecycle", type=ltype or "unknown", timestamp=ts,
                    anchor_uuid=uuid, label=label,
                    detail=_lifecycle_detail(ltype, obj) or label)

    return events


def _compaction_detail(meta: dict) -> str:
    lines = []
    if meta.get("trigger"):
        lines.append(f"Trigger: {meta['trigger']}")
    if isinstance(meta.get("preTokens"), int):
        lines.append(f"Context before: {meta['preTokens']:,} tokens")
    if isinstance(meta.get("durationMs"), (int, float)):
        lines.append(f"Compaction took: {round(meta['durationMs'] / 1000)}s")
    tools = meta.get("preCompactDiscoveredTools")
    if isinstance(tools, list) and tools:
        lines.append(f"Discovered tools carried over: {', '.join(map(str, tools))}")
    return "\n".join(lines) or "Conversation compacted"


def _lifecycle_label(ltype: Optional[str], obj: dict) -> Optional[str]:
    if ltype == "ai-title":
        return f"title → {obj.get('aiTitle', '')}"
    if ltype == "permission-mode":
        return f"permission → {obj.get('permissionMode', '')}"
    if ltype == "mode":
        return f"mode → {obj.get('mode', '')}"
    if ltype == "queue-operation":
        return f"{obj.get('operation', 'queue')}: {str(obj.get('content', ''))[:80]}"
    if ltype == "pr-link":
        return f"PR #{obj.get('prNumber', '?')} · {obj.get('prRepository', '')}"
    if ltype == "attachment":
        return f"attachment: {(obj.get('attachment') or {}).get('type', '')}"
    if ltype == "agent-name":
        return f"named: {obj.get('agentName', '')}"
    if ltype == "file-history-snapshot":
        return "file snapshot"
    # last-prompt duplicates a user prompt; other unknown types are dropped.
    return None


def _lifecycle_detail(ltype: Optional[str], obj: dict) -> Optional[str]:
    """Fuller text for the detail pane (the label is the one-line summary)."""
    if ltype == "queue-operation":
        return str(obj.get("content") or "")
    if ltype == "pr-link":
        url = obj.get("prUrl")
        return str(url) if url else None
    if ltype == "attachment":
        att = obj.get("attachment") or {}
        return str(att.get("content") or att.get("text") or "") or None
    return None
