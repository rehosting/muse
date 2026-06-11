"""Gemini CLI provider (read-only).

Gemini stores transcripts at ~/.gemini/tmp/<project>/chats/session-*.jsonl —
newline-delimited, with a header line {sessionId, projectHash, kind:"main"} then
typed message lines:
  - user   {id, timestamp, type:"user", content:[{text}]}
  - gemini {id, timestamp, type:"gemini", content:str, thoughts:[{subject,description}],
            toolCalls:[{id,name,args}], model, tokens}
  - info / error / warning {id, timestamp, type, content}
  - {"$set": {...}}  metadata updates (skipped)

Tool *results* aren't stored inline; we best-effort pair them from the persisted
files under tool-outputs/session-<id>/ (filenames embed the call's "<ts>_<idx>").
One assistant `gemini` line can carry thinking + text + several tool calls, so it
maps to one ThreadItem with multiple blocks. Item uuids are "gm{line_index}".
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Optional

from ..config import get_settings
from ..models import (
    ContentBlock,
    FileChange,
    FileOp,
    SessionEvent,
    SessionSummary,
    Thread,
    ThreadItem,
    ToolResult,
    ToolUse,
)
from ..transcript import iter_json_lines
from .base import IndexDoc, Provider, SearchRow

_PREFIX = "gemini:"
_MAX_BODY = 4000
_FILE_TOOLS = {"read_file": "read", "write_file": "write", "replace": "edit", "edit": "edit"}
_FILE_ARG_KEYS = ("file_path", "path", "absolute_path")
_summary_cache: dict[str, tuple[float, SessionSummary]] = {}
_cwd_map: Optional[dict[str, str]] = None
# file tail like "_1778006485541_0_0lrtzp.txt" → call ts_idx "1778006485541_0"
_OUT_RE = re.compile(r"(\d{10,16}_\d+)_[^_/]+\.txt$")
_CALL_TAIL_RE = re.compile(r"(\d{10,16}_\d+)$")


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
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("text"))
    return ""


def _thoughts_text(thoughts: Any) -> str:
    if not isinstance(thoughts, list):
        return ""
    out = []
    for t in thoughts:
        if isinstance(t, dict):
            subj, desc = t.get("subject", ""), t.get("description", "")
            out.append(f"{subj}: {desc}".strip(": ").strip())
    return "\n".join(p for p in out if p)


def _cwd_for(project: str) -> Optional[str]:
    global _cwd_map
    if _cwd_map is None:
        _cwd_map = {}
        try:
            data = json.loads((get_settings().gemini_dir / "projects.json").read_bytes())
            for path, name in (data.get("projects") or {}).items():
                _cwd_map[name] = path
        except (OSError, json.JSONDecodeError, AttributeError):
            _cwd_map = {}
    return _cwd_map.get(project)


def _first_line(text: str, n: int = 100) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0]
    return first[:n] + ("…" if len(first) > n or "\n" in text else "")


def _tool_summary(name: str, args: dict) -> str:
    for k in ("command", "file_path", "path", "pattern", "dir_path", "query", "description"):
        if args.get(k):
            v = args[k]
            return " ".join(map(str, v)) if isinstance(v, list) else str(v)
    return ""


def _chat_files() -> list[Path]:
    root = get_settings().gemini_dir / "tmp"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/chats/session-*.jsonl"))


def _project_of(path: Path) -> str:
    # .../tmp/<project>/chats/<file>
    return path.parent.parent.name


def _session_id(raw_lines: list[dict]) -> Optional[str]:
    for obj in raw_lines:
        if obj.get("kind") == "main" and obj.get("sessionId"):
            return obj["sessionId"]
    return None


def _header_sid(path: Path) -> Optional[str]:
    """Read just the header line for the real sessionId (filename stem ≠ id)."""
    try:
        with path.open("rb") as fh:
            head = json.loads(fh.readline())
        return head.get("sessionId")
    except (OSError, json.JSONDecodeError):
        return None


def _result_map(project: str, sid: str) -> dict[str, Path]:
    outdir = get_settings().gemini_dir / "tmp" / project / "tool-outputs" / f"session-{sid}"
    if not outdir.is_dir():
        return {}
    idx: dict[str, Path] = {}
    try:
        for p in outdir.iterdir():
            m = _OUT_RE.search(p.name)
            if m:
                idx.setdefault(m.group(1), p)
    except OSError:
        return {}
    return idx


def _result_for(call_id: str, results: dict[str, Path]) -> Optional[ToolResult]:
    m = _CALL_TAIL_RE.search(call_id or "")
    if not m:
        return None
    path = results.get(m.group(1))
    if not path:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    truncated = len(text) > _MAX_BODY
    return ToolResult(tool_use_id=call_id, content=text[:_MAX_BODY], truncated=truncated)


def _build_items(raw: list[dict], results: dict[str, Path]) -> list[ThreadItem]:
    items: list[ThreadItem] = []
    for i, obj in enumerate(raw):
        t = obj.get("type")
        uuid = f"gm{i}"
        ts = _ts(obj.get("timestamp"))
        if t == "user":
            text = _content_text(obj.get("content"))
            if text.strip():
                items.append(ThreadItem(uuid=uuid, role="user", type="user", timestamp=ts, text=text))
        elif t == "gemini":
            blocks: list[ContentBlock] = []
            thoughts = _thoughts_text(obj.get("thoughts"))
            if thoughts:
                blocks.append(ContentBlock(kind="thinking", text=thoughts))
            text = _content_text(obj.get("content"))
            if text.strip():
                blocks.append(ContentBlock(kind="text", text=text))
            for tc in obj.get("toolCalls") or []:
                if not isinstance(tc, dict):
                    continue
                tu = ToolUse(id=tc.get("id", uuid), name=tc.get("name", "tool"), input=tc.get("args") or {})
                tu.result = _result_for(tu.id, results)
                blocks.append(ContentBlock(kind="tool_use", tool_use=tu))
            if blocks:
                items.append(ThreadItem(uuid=uuid, role="assistant", type="gemini", timestamp=ts,
                                        model=obj.get("model"), blocks=blocks))
        elif t in ("info", "error", "warning"):
            text = _content_text(obj.get("content")) or str(obj.get("content") or "")
            if text.strip():
                items.append(ThreadItem(uuid=uuid, role="system", type=t, timestamp=ts,
                                        level="error" if t == "error" else t, text=text))
    return items


def _title(items: list[ThreadItem]) -> tuple[str, str]:
    fallback: Optional[str] = None
    for item in items:
        if item.role != "user" or not item.text or not item.text.strip():
            continue
        line = item.text.strip().splitlines()[0]
        if fallback is None:
            fallback = line[:120]
        if line.startswith("/"):
            continue  # skip slash-commands like "/model"
        return line[:120], "user"
    return (fallback, "user") if fallback else ("Gemini session", "none")


def _model(items: list[ThreadItem]) -> Optional[str]:
    for item in items:
        if item.model:
            return item.model
    return None


class GeminiProvider(Provider):
    id = "gemini"
    display_name = "Gemini"
    prefix = _PREFIX

    def _find_file(self, session_id: str) -> Optional[Path]:
        raw = self.raw_id(session_id)
        for path in _chat_files():
            try:
                with path.open("rb") as fh:
                    first = fh.readline()
                head = json.loads(first)
            except (OSError, json.JSONDecodeError):
                continue
            if head.get("sessionId") == raw:
                return path
        return None

    def _summary(self, path: Path) -> Optional[SessionSummary]:
        try:
            mtime, size = path.stat().st_mtime, path.stat().st_size
        except OSError:
            return None
        cached = _summary_cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        # Gemini sessions get huge (tens of thousands of tool-call lines, multi-MB).
        # For the listing, read only the head (sid/title/model appear near the top)
        # and ESTIMATE the message count from message density — never read whole
        # multi-MB files just to list them. The viewer shows exact items.
        head_n = 256 * 1024
        try:
            with path.open("rb") as fh:
                head = fh.read(head_n)
        except OSError:
            return None
        head_msgs = head.count(b'"type":')
        count = head_msgs if size <= len(head) else round(head_msgs * size / max(1, len(head)))
        sid = model = title = fallback = None
        title_src = "none"
        for raw in head.split(b"\n"):
            if title is not None and model is not None and sid is not None:
                break
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue  # likely the truncated last line of the head
            if obj.get("kind") == "main":
                sid = obj.get("sessionId")
                continue
            t = obj.get("type")
            if t == "user" and title is None:
                txt = _content_text(obj.get("content")).strip()
                if txt:
                    line = txt.splitlines()[0][:120]
                    fallback = fallback or line
                    if not line.startswith("/"):
                        title, title_src = line, "user"
            elif t == "gemini" and model is None:
                model = obj.get("model")
        if not sid:
            return None
        project = _project_of(path)
        summary = SessionSummary(
            session_id=f"{_PREFIX}{sid}",
            provider="gemini",
            project_cwd=_cwd_for(project) or project,
            project_dir=project,
            title=title or fallback or "Gemini session",
            title_source=title_src if title else ("user" if fallback else "none"),  # type: ignore[arg-type]
            message_count=count,
            model=model,
            mtime=datetime.fromtimestamp(mtime, tz=timezone.utc),
            size_bytes=size,
            state="stopped",
        )
        _summary_cache[str(path)] = (mtime, summary)
        return summary

    def iter_sessions(self) -> list[SessionSummary]:
        out = []
        for path in _chat_files():
            s = self._summary(path)
            if s:
                out.append(s)
        return out

    def load_thread(self, session_id: str) -> Optional[Thread]:
        path = self._find_file(session_id)
        if path is None:
            return None
        raw = list(iter_json_lines(path))
        sid = _session_id(raw) or self.raw_id(session_id)
        results = _result_map(_project_of(path), sid)
        items = _build_items(raw, results)
        title, source = _title(items)
        return Thread(
            session_id=f"{_PREFIX}{sid}",
            provider="gemini",
            project_cwd=_cwd_for(_project_of(path)) or _project_of(path),
            title=title,
            title_source=source,  # type: ignore[arg-type]
            model=_model(items),
            items=items,
        )

    def build_events(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[SessionEvent]]:
        path = self._find_file(session_id)
        if path is None:
            return None
        raw = list(iter_json_lines(path))
        events: list[SessionEvent] = []
        n = 0

        def add(**kw):
            nonlocal n
            events.append(SessionEvent(index=n, **kw))
            n += 1

        for i, obj in enumerate(raw):
            t = obj.get("type")
            uuid = f"gm{i}"
            ts = _ts(obj.get("timestamp"))
            if t == "user":
                text = _content_text(obj.get("content"))
                if text.strip():
                    add(kind="user", type="user", role="user", timestamp=ts, anchor_uuid=uuid,
                        label=_first_line(text), detail=text)
            elif t == "gemini":
                thoughts = _thoughts_text(obj.get("thoughts"))
                if thoughts:
                    add(kind="thinking", type="gemini", role="assistant", timestamp=ts,
                        anchor_uuid=uuid, label="thinking", detail=thoughts)
                text = _content_text(obj.get("content"))
                if text.strip():
                    add(kind="assistant_text", type="gemini", role="assistant", timestamp=ts,
                        anchor_uuid=uuid, label=_first_line(text), detail=text)
                for tc in obj.get("toolCalls") or []:
                    if not isinstance(tc, dict):
                        continue
                    name = tc.get("name", "tool")
                    add(kind="tool_call", type="gemini", role="assistant", timestamp=ts,
                        anchor_uuid=uuid, tool_use_id=tc.get("id"), tool_name=name,
                        label=f"{name}({_tool_summary(name, tc.get('args') or {})})"[:140])
            elif t in ("info", "error", "warning"):
                text = _content_text(obj.get("content")) or str(obj.get("content") or "")
                if text.strip():
                    add(kind="system", type=t, role="system", timestamp=ts, anchor_uuid=uuid,
                        label=_first_line(text), detail=text, is_error=(t == "error"),
                        level="error" if t == "error" else t)
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
                if not tu or tu.name not in _FILE_TOOLS:
                    continue
                path = next((str(tu.input[k]) for k in _FILE_ARG_KEYS if tu.input.get(k)), None)
                if not path:
                    continue
                kind = _FILE_TOOLS[tu.name]
                fc = by_path.setdefault(path, FileChange(path=path))
                fc.ops.append(FileOp(tool_use_id=tu.id, kind=kind, tool_name=tu.name,
                                     timestamp=item.timestamp,
                                     is_error=bool(tu.result and tu.result.is_error)))
                if kind == "read":
                    fc.read_count += 1
                elif kind == "write":
                    fc.write_count += 1
                else:
                    fc.edit_count += 1
                if item.timestamp:
                    fc.first_ts = fc.first_ts or item.timestamp
                    fc.last_ts = item.timestamp
        return sorted(by_path.values(), key=lambda f: len(f.ops), reverse=True)

    def search_docs(self) -> list[IndexDoc]:
        docs: list[IndexDoc] = []
        for path in _chat_files():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            sid = _header_sid(path)
            if not sid:
                continue
            docs.append(IndexDoc(path=str(path), mtime=mtime, session_id=f"{_PREFIX}{sid}",
                                 project_dir=_project_of(path), rows_fn=partial(_search_rows, path),
                                 append_safe=False))
        return docs


def _search_rows(path: Path, offset: int, start_index: int) -> tuple[list[SearchRow], int, int]:
    """Gemini chat files aren't guaranteed append-only (a save can rewrite the file),
    so this is registered append_safe=False and always called for a full re-index;
    `offset`/`start_index` are unused. Signature matches the RowsFn contract."""
    rows: list[SearchRow] = []
    n = 0
    for i, obj in enumerate(iter_json_lines(path)):
        n = i + 1
        t = obj.get("type")
        uuid = f"gm{i}"
        ts = obj.get("timestamp")
        if t == "user":
            text = _content_text(obj.get("content"))
            if text.strip():
                rows.append((uuid, "user", ts, text[:_MAX_BODY]))
        elif t == "gemini":
            parts = [_content_text(obj.get("content")), _thoughts_text(obj.get("thoughts"))]
            for tc in obj.get("toolCalls") or []:
                if isinstance(tc, dict):
                    parts.append(f"{tc.get('name', '')} {_tool_summary(tc.get('name', ''), tc.get('args') or {})}")
            body = "\n".join(p for p in parts if p and p.strip()).strip()
            if body:
                rows.append((uuid, "assistant", ts, body[:_MAX_BODY]))
    return rows, 0, n
