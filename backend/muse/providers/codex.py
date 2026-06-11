"""OpenAI Codex CLI provider (read-only).

Codex stores transcripts at ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.
Each line is a wrapper {timestamp, type, payload}:
  - session_meta   payload: {id, cwd, cli_version, model_provider, ...}
  - turn_context   payload: {turn_id, cwd, ...}
  - event_msg      payload: {type, model_context_window, model?, ...}
  - response_item  payload.type ∈ {message, reasoning, function_call,
                   function_call_output, custom_tool_call, custom_tool_call_output}
  - compacted      payload: {message, replacement_history}

We map these onto muse's normalized models. Tool calls/outputs pair by `call_id`
(the analogue of Claude's tool_use_id). Item uuids are synthesized from the line
index ("ci{i}") so the conversation, timeline, and search all cross-reference.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Optional

from ..config import get_settings
from ..incremental import new_objects
from ..models import (
    ContentBlock,
    FileChange,
    FileOp,
    SessionEvent,
    SessionLineage,
    SessionSummary,
    Thread,
    ThreadItem,
    ToolResult,
    ToolUse,
)
from ..transcript import iter_json_lines
from .base import IndexDoc, Provider, SearchRow

_PREFIX = "codex:"
_TOOL_INPUT_KEYS = ("command", "file_path", "path", "query", "url", "cmd")
# per-file summary cache keyed by path -> (mtime, SessionSummary)
_summary_cache: dict[str, tuple[float, SessionSummary]] = {}


def _ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("text")
        )
    return ""


def _reasoning_text(payload: dict) -> str:
    summary = payload.get("summary")
    if isinstance(summary, list):
        text = "\n".join(
            s.get("text", "") for s in summary if isinstance(s, dict) and s.get("text")
        )
        if text.strip():
            return text
    return _content_text(payload.get("content")) or ""


def _compaction_text(payload: dict) -> str:
    """Codex's compaction summary: `message` is usually empty; the narrative is the
    `replacement_history` (the condensed messages that replaced prior context)."""
    msg = payload.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg
    rh = payload.get("replacement_history")
    if isinstance(rh, list):
        parts = []
        for item in rh:
            if not isinstance(item, dict):
                continue
            text = _content_text(item.get("content"))
            if text.strip():
                role = item.get("role", "")
                parts.append(f"[{role}] {text}" if role else text)
        return "\n\n".join(parts)
    return ""


def _tool_input(payload: dict) -> dict[str, Any]:
    if payload.get("type") == "function_call":
        args = payload.get("arguments")
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                return parsed if isinstance(parsed, dict) else {"arguments": parsed}
            except json.JSONDecodeError:
                return {"arguments": args}
        return args if isinstance(args, dict) else {}
    # custom_tool_call: input is a raw string (e.g. an apply_patch body)
    inp = payload.get("input")
    return {"input": inp} if inp is not None else {}


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)[:8000]
    except (TypeError, ValueError):
        return str(value)[:8000]


def _tool_summary(name: str, inp: dict) -> str:
    for key in _TOOL_INPUT_KEYS:
        if inp.get(key):
            v = inp[key]
            return " ".join(map(str, v)) if isinstance(v, list) else str(v)
    vals = [v for v in inp.values() if v]
    return str(vals[0])[:120] if vals else ""


def _first_line(text: str, n: int = 100) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0]
    return first[:n] + ("…" if len(first) > n or "\n" in text else "")


def _rollout_files() -> list[Path]:
    root = get_settings().codex_dir / "sessions"
    if not root.is_dir():
        return []
    return sorted(root.rglob("rollout-*.jsonl"))


def _uuid_from_name(path: Path) -> str:
    # rollout-<YYYY-MM-DDTHH-MM-SS>-<uuid(5 dash groups)>.jsonl
    parts = path.stem.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else path.stem


# ---- shared raw → normalized walk ----------------------------------------


def _load_raw(session_id: str) -> Optional[tuple[Path, list[dict]]]:
    raw_id = session_id[len(_PREFIX):] if session_id.startswith(_PREFIX) else session_id
    root = get_settings().codex_dir / "sessions"
    if not root.is_dir():
        return None
    matches = list(root.rglob(f"*{raw_id}.jsonl"))
    if not matches:
        return None
    return matches[0], list(iter_json_lines(matches[0]))


def _token_total(raw: list[dict]) -> int:
    """Codex emits cumulative `token_count` event_msgs; the last one holds the
    session totals. We report real work — non-cached input + output + reasoning —
    excluding cached_input_tokens (which `input_tokens` includes) so the count
    isn't dominated by cache re-reads."""
    total = 0
    for obj in raw:
        if obj.get("type") != "event_msg":
            continue
        p = obj.get("payload") or {}
        if p.get("type") != "token_count":
            continue
        u = (p.get("info") or {}).get("total_token_usage") or {}
        if not isinstance(u, dict):
            continue
        inp = u.get("input_tokens", 0) or 0
        cached = u.get("cached_input_tokens", 0) or 0
        out = u.get("output_tokens", 0) or 0
        reasoning = u.get("reasoning_output_tokens", 0) or 0
        total = max(0, inp - cached) + out + reasoning
    return total


def _meta(raw: list[dict]) -> dict[str, Any]:
    cwd = model = version = context_window = None
    for obj in raw:
        t = obj.get("type")
        p = obj.get("payload") or {}
        if t == "session_meta":
            cwd = cwd or p.get("cwd")
            version = version or p.get("cli_version")
            model = model or p.get("model")
        elif t in ("event_msg", "turn_context"):
            context_window = context_window or p.get("model_context_window")
            model = model or p.get("model")
        if cwd and model and context_window:
            break
    return {"cwd": cwd, "model": model, "version": version, "context_window": context_window}


def _build_items(raw: list[dict]) -> tuple[list[ThreadItem], dict[str, ToolResult], dict[str, str]]:
    """Return (items, results-by-call_id, call_id->tool_call_uuid)."""
    items: list[ThreadItem] = []
    results: dict[str, ToolResult] = {}
    call_uuid: dict[str, str] = {}
    for i, obj in enumerate(raw):
        if obj.get("type") != "response_item":
            continue
        p = obj.get("payload") or {}
        pt = p.get("type")
        uuid = f"ci{i}"
        ts = _ts(obj.get("timestamp"))
        if pt == "message":
            role = {"developer": "system", "tool": "user"}.get(p.get("role", "assistant"), p.get("role", "assistant"))
            text = _content_text(p.get("content"))
            if not text.strip():
                continue
            if role == "user":
                items.append(ThreadItem(uuid=uuid, role="user", type="message", timestamp=ts, text=text))
            elif role == "system":
                items.append(ThreadItem(uuid=uuid, role="system", type="message", timestamp=ts, text=text))
            else:
                items.append(ThreadItem(uuid=uuid, role="assistant", type="message", timestamp=ts,
                                        blocks=[ContentBlock(kind="text", text=text)]))
        elif pt == "reasoning":
            items.append(ThreadItem(uuid=uuid, role="assistant", type="reasoning", timestamp=ts,
                                    blocks=[ContentBlock(kind="thinking", text=_reasoning_text(p))]))
        elif pt in ("function_call", "custom_tool_call"):
            call_id = p.get("call_id") or uuid
            tu = ToolUse(id=call_id, name=p.get("name", "tool"), input=_tool_input(p))
            items.append(ThreadItem(uuid=uuid, role="assistant", type=pt, timestamp=ts,
                                    blocks=[ContentBlock(kind="tool_use", tool_use=tu)]))
            call_uuid[call_id] = uuid
        elif pt in ("function_call_output", "custom_tool_call_output"):
            call_id = p.get("call_id")
            if call_id:
                content = _stringify(p.get("output"))
                results[call_id] = ToolResult(
                    tool_use_id=call_id, content=content, is_error="error" in content[:200].lower()
                )
    for item in items:
        for b in item.blocks:
            if b.tool_use and b.tool_use.id in results:
                b.tool_use.result = results[b.tool_use.id]
    return items, results, call_uuid


def _title(items: list[ThreadItem]) -> tuple[str, str]:
    # Codex injects an <environment_context> (and sometimes <user_instructions>)
    # as the first user turn(s); the real prompt is the first plain user message.
    fallback: Optional[str] = None
    for item in items:
        if item.role != "user" or not item.text or not item.text.strip():
            continue
        text = item.text.strip()
        if fallback is None:
            fallback = text.splitlines()[0][:120]
        if text.startswith("<"):
            continue  # skip injected XML-ish context wrappers
        return text.splitlines()[0][:120], "user"
    return (fallback, "user") if fallback else ("Codex session", "none")


# ---- provider ------------------------------------------------------------


class CodexProvider(Provider):
    id = "codex"
    display_name = "Codex"
    prefix = _PREFIX

    def _summary(self, path: Path) -> Optional[SessionSummary]:
        try:
            mtime = path.stat().st_mtime
            size = path.stat().st_size
        except OSError:
            return None
        cached = _summary_cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        raw = list(iter_json_lines(path))
        if not raw:
            return None
        meta = _meta(raw)
        items, _r, _c = _build_items(raw)
        title, source = _title(items)
        uuid = _uuid_from_name(path)
        root = get_settings().codex_dir / "sessions"
        try:
            project_dir = str(path.parent.relative_to(root))
        except ValueError:
            project_dir = "codex"
        summary = SessionSummary(
            session_id=f"{_PREFIX}{uuid}",
            provider="codex",
            project_cwd=meta["cwd"],
            project_dir=project_dir,
            title=title,
            title_source=source,  # type: ignore[arg-type]
            message_count=sum(1 for it in items if it.role in ("user", "assistant")),
            total_tokens=_token_total(raw),
            model=meta["model"],
            mtime=datetime.fromtimestamp(mtime, tz=timezone.utc),
            size_bytes=size,
            state="stopped",  # Codex sessions aren't live-tracked by muse
        )
        _summary_cache[str(path)] = (mtime, summary)
        return summary

    def iter_sessions(self) -> list[SessionSummary]:
        out = []
        for path in _rollout_files():
            s = self._summary(path)
            if s:
                out.append(s)
        return out

    def load_thread(self, session_id: str) -> Optional[Thread]:
        loaded = _load_raw(session_id)
        if loaded is None:
            return None
        path, raw = loaded
        meta = _meta(raw)
        items, _results, _cu = _build_items(raw)
        title, source = _title(items)
        return Thread(
            session_id=session_id if session_id.startswith(_PREFIX) else f"{_PREFIX}{session_id}",
            provider="codex",
            project_cwd=meta["cwd"],
            version=meta["version"],
            title=title,
            title_source=source,  # type: ignore[arg-type]
            model=meta["model"],
            context_window=meta["context_window"],
            items=items,
        )

    def build_events(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[SessionEvent]]:
        loaded = _load_raw(session_id)
        if loaded is None:
            return None
        _path, raw = loaded
        _items, results, call_uuid = _build_items(raw)
        # tool-call timestamps for duration calc
        call_ts: dict[str, datetime] = {}
        events: list[SessionEvent] = []
        n = 0

        def add(**kw):
            nonlocal n
            events.append(SessionEvent(index=n, **kw))
            n += 1

        for i, obj in enumerate(raw):
            t = obj.get("type")
            p = obj.get("payload") or {}
            ts = _ts(obj.get("timestamp"))
            uuid = f"ci{i}"
            if t == "response_item":
                pt = p.get("type")
                if pt == "message":
                    role = {"developer": "system", "tool": "user"}.get(p.get("role", "assistant"), p.get("role", "assistant"))
                    text = _content_text(p.get("content"))
                    if not text.strip():
                        continue
                    if role == "user":
                        add(kind="user", type="message", role="user", timestamp=ts, anchor_uuid=uuid, label=_first_line(text), detail=text)
                    elif role == "system":
                        add(kind="system", type="message", role="system", timestamp=ts, anchor_uuid=uuid, label=_first_line(text), detail=text)
                    else:
                        add(kind="assistant_text", type="message", role="assistant", timestamp=ts, anchor_uuid=uuid, label=_first_line(text), detail=text)
                elif pt == "reasoning":
                    add(kind="thinking", type="reasoning", role="assistant", timestamp=ts, anchor_uuid=uuid, label="thinking", detail=_reasoning_text(p))
                elif pt in ("function_call", "custom_tool_call"):
                    call_id = p.get("call_id") or uuid
                    name = p.get("name", "tool")
                    if ts:
                        call_ts[call_id] = ts
                    add(kind="tool_call", type=pt, role="assistant", timestamp=ts, anchor_uuid=uuid,
                        tool_use_id=call_id, tool_name=name,
                        label=f"{name}({_tool_summary(name, _tool_input(p))})"[:140])
                elif pt in ("function_call_output", "custom_tool_call_output"):
                    call_id = p.get("call_id")
                    res = results.get(call_id or "")
                    dur = None
                    if call_id in call_ts and ts:
                        dur = int((ts - call_ts[call_id]).total_seconds() * 1000)
                    add(kind="tool_result", type=pt, role="user", timestamp=ts,
                        anchor_uuid=call_uuid.get(call_id or "", uuid), tool_use_id=call_id,
                        status="error" if (res and res.is_error) else "ok",
                        is_error=bool(res and res.is_error), duration_ms=dur,
                        label=_first_line(res.content if res else "", 100))
            elif t == "compacted":
                add(kind="system", type="compacted", role="system", timestamp=ts, anchor_uuid=uuid,
                    label="Context compacted", is_compaction=True, detail=_compaction_text(p))
            elif t in ("event_msg", "turn_context", "session_meta"):
                label = _lifecycle_label(t, p)
                if label:
                    add(kind="lifecycle", type=t, timestamp=ts, anchor_uuid=uuid, label=label)
        return events

    def build_file_changes(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[FileChange]]:
        thread = self.load_thread(session_id)
        if thread is None:
            return None
        by_path: dict[str, FileChange] = {}
        for item in thread.items:
            for b in item.blocks:
                tu = b.tool_use
                if not tu:
                    continue
                for path, kind in _apply_patch_paths(tu):
                    fc = by_path.setdefault(path, FileChange(path=path))
                    fc.ops.append(FileOp(tool_use_id=tu.id, kind=kind, tool_name=tu.name,
                                         timestamp=item.timestamp,
                                         is_error=bool(tu.result and tu.result.is_error)))
                    if kind == "write":
                        fc.write_count += 1
                    else:
                        fc.edit_count += 1
                    if tu.result and tu.result.is_error:
                        fc.error_count += 1
                    if item.timestamp:
                        fc.first_ts = fc.first_ts or item.timestamp
                        fc.last_ts = item.timestamp
        return sorted(by_path.values(), key=lambda f: len(f.ops), reverse=True)

    def build_lineage(self, session_id: str) -> Optional[SessionLineage]:
        loaded = _load_raw(session_id)
        if loaded is None:
            return None
        _p, raw = loaded
        n = sum(1 for o in raw if o.get("type") == "compacted")
        return SessionLineage(session_id=session_id, segment_count=n + 1)

    def search_docs(self) -> list[IndexDoc]:
        docs: list[IndexDoc] = []
        root = get_settings().codex_dir / "sessions"
        for path in _rollout_files():
            try:
                st = path.stat()
            except OSError:
                continue
            try:
                project_dir = str(path.parent.relative_to(root))
            except ValueError:
                project_dir = "codex"
            docs.append(IndexDoc(path=str(path), mtime=st.st_mtime,
                                 session_id=f"{_PREFIX}{_uuid_from_name(path)}",
                                 project_dir=project_dir, rows_fn=partial(_search_rows, path),
                                 size=st.st_size))
        return docs


_APPLY_PATCH_RE = re.compile(r"\*\*\*\s+(Add|Update|Delete)\s+File:\s+(.+)")


def _apply_patch_paths(tu: ToolUse) -> list[tuple[str, str]]:
    """Best-effort file paths from a Codex apply_patch tool call."""
    if tu.name not in ("apply_patch", "shell"):
        return []
    blob = tu.input.get("input") or tu.input.get("arguments") or ""
    if not isinstance(blob, str):
        blob = _stringify(blob)
    out = []
    for m in _APPLY_PATCH_RE.finditer(blob):
        verb, path = m.group(1), m.group(2).strip()
        out.append((path, "write" if verb == "Add" else "edit"))
    return out


def _lifecycle_label(t: str, p: dict) -> Optional[str]:
    if t == "session_meta":
        return f"session start · {p.get('model_provider', 'codex')}"
    if t == "turn_context":
        return None  # noisy; skip
    if t == "event_msg":
        sub = p.get("type")
        if sub in ("task_started", "task_complete", "token_count"):
            return None
        return str(sub) if sub else None
    return None


def _search_rows(path: Path, offset: int, start_index: int) -> tuple[list[SearchRow], int, int]:
    """Append-only: parse only the lines after `offset`, numbering rows `ci{i}` from
    `start_index` so the ids stay aligned with build_events' `enumerate(raw)` (raw is
    the same iter_json_lines stream). Returns (rows, new_offset, new_index)."""
    objs, new_offset = new_objects(path, offset)
    rows: list[SearchRow] = []
    for k, obj in enumerate(objs):
        i = start_index + k
        if obj.get("type") != "response_item":
            continue
        p = obj.get("payload") or {}
        pt = p.get("type")
        uuid = f"ci{i}"
        ts = obj.get("timestamp")
        if pt == "message":
            text = _content_text(p.get("content"))
            role = p.get("role", "assistant")
            if text.strip():
                rows.append((uuid, role, ts, text[:4000]))
        elif pt == "reasoning":
            text = _reasoning_text(p)
            if text.strip():
                rows.append((uuid, "assistant", ts, text[:4000]))
        elif pt in ("function_call", "custom_tool_call"):
            inp = _tool_input(p)
            rows.append((uuid, "assistant", ts, f"{p.get('name', '')} {_tool_summary(p.get('name', ''), inp)}".strip()[:4000]))
    return rows, new_offset, start_index + len(objs)
