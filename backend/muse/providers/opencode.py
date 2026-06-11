"""opencode provider (read-only).

Unlike the JSONL-based providers, opencode keeps everything in a single SQLite
database at ~/.local/share/opencode/opencode.db. The relevant tables:

  session(id, project_id, parent_id, directory, title, version,
          time_created, time_updated, ...)
  message(id, session_id, time_created, data)   data = JSON envelope:
          {role, modelID, providerID, time:{created,completed}, error?, ...}
  part(id, message_id, session_id, time_created, data)  data = JSON, one of:
          {type:"text", text}
          {type:"reasoning", text, time:{start,end}}
          {type:"tool", tool, callID, state:{status, input, output, time}}
          {type:"step-start"|"step-finish", tokens?, cost?}
  project(id, worktree, name)

One assistant *message* aggregates many parts (a full turn), so it maps to a
single assistant ThreadItem with [thinking?, text?, tool_use*] blocks — the same
shape as the Gemini adapter. Tool results are inline (state.output), so pairing
is trivial; no cross-message call_id matching is needed. Item uuids are the
opencode message ids, so the conversation, timeline, and search cross-reference.

The DB is opened strictly read-only (mode=ro) — muse never writes to opencode's
data directory (verified: no -wal/-shm modification on read).
"""

from __future__ import annotations

import json
import sqlite3
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
    SessionLineage,
    SessionSummary,
    Thread,
    ThreadItem,
    ToolResult,
    ToolUse,
)
from .base import IndexDoc, Provider, SearchRow

_PREFIX = "opencode:"
_FILE_TOOLS = {"read": "read", "write": "write", "edit": "edit"}
_TOOL_INPUT_KEYS = ("filePath", "file_path", "path", "command", "pattern", "query", "url")
# Cache the full session list keyed by the DB's mtime so the 3s-polled
# list_sessions() does no work while opencode is idle.
_sessions_cache: tuple[float, list[SessionSummary]] | None = None


def _db_path() -> Path:
    return get_settings().opencode_dir / "opencode.db"


def _db_mtime() -> Optional[float]:
    """Newest mtime across the DB and its WAL — the WAL changes on every write,
    so this flips whenever opencode records anything."""
    db = _db_path()
    if not db.is_file():
        return None
    m = db.stat().st_mtime
    wal = db.with_name(db.name + "-wal")
    if wal.is_file():
        m = max(m, wal.stat().st_mtime)
    return m


def _connect() -> Optional[sqlite3.Connection]:
    db = _db_path()
    if not db.is_file():
        return None
    # mode=ro: read-only, never creates/modifies -wal/-shm in opencode's dir.
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ts(ms: Any) -> Optional[datetime]:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _loads(data: Any) -> dict:
    if isinstance(data, (str, bytes)):
        try:
            obj = json.loads(data)
            return obj if isinstance(obj, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return data if isinstance(data, dict) else {}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)[:8000]
    except (TypeError, ValueError):
        return str(value)[:8000]


def _first_line(text: str, n: int = 100) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0]
    return first[:n] + ("…" if len(first) > n or "\n" in text else "")


def _tool_summary(inp: dict) -> str:
    for key in _TOOL_INPUT_KEYS:
        v = inp.get(key)
        if v:
            return " ".join(map(str, v)) if isinstance(v, list) else str(v)
    vals = [v for v in inp.values() if v]
    return str(vals[0])[:120] if vals else ""


def _muse_id(raw_id: str) -> str:
    return raw_id if raw_id.startswith(_PREFIX) else f"{_PREFIX}{raw_id}"


# ---- raw → normalized ----------------------------------------------------


def _parts_for(conn: sqlite3.Connection, session_id: str) -> dict[str, list[dict]]:
    """message_id -> [part data dicts] in chronological order."""
    by_msg: dict[str, list[dict]] = {}
    rows = conn.execute(
        "SELECT message_id, data FROM part WHERE session_id=? ORDER BY time_created, id",
        (session_id,),
    ).fetchall()
    for r in rows:
        by_msg.setdefault(r["message_id"], []).append(_loads(r["data"]))
    return by_msg


def _tool_block(part: dict) -> Optional[ContentBlock]:
    state = part.get("state") or {}
    name = part.get("tool", "tool")
    call_id = part.get("callID") or part.get("id") or name
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    status = state.get("status")
    is_error = status == "error"
    out = state.get("output")
    if out is None and is_error:
        out = state.get("error")
    result = None
    if out is not None or is_error or status == "completed":
        result = ToolResult(
            tool_use_id=call_id, content=_stringify(out), is_error=is_error
        )
    tu = ToolUse(id=call_id, name=name, input=inp, result=result)
    return ContentBlock(kind="tool_use", tool_use=tu)


def _message_item(
    msg_id: str, msg: dict, parts: list[dict], ts: Optional[datetime]
) -> Optional[ThreadItem]:
    role = msg.get("role", "assistant")
    if role == "user":
        text = "\n".join(
            p.get("text", "") for p in parts if p.get("type") == "text" and p.get("text")
        )
        if not text.strip():
            return None
        return ThreadItem(uuid=msg_id, role="user", type="message", timestamp=ts, text=text)

    blocks: list[ContentBlock] = []
    for p in parts:
        pt = p.get("type")
        if pt == "reasoning" and p.get("text", "").strip():
            blocks.append(ContentBlock(kind="thinking", text=p["text"]))
        elif pt == "text" and p.get("text", "").strip():
            blocks.append(ContentBlock(kind="text", text=p["text"]))
        elif pt == "tool":
            blk = _tool_block(p)
            if blk:
                blocks.append(blk)
    # Surface a turn-level API error (e.g. "model not supported") as text.
    err = msg.get("error")
    if isinstance(err, dict):
        emsg = (err.get("data") or {}).get("message") or err.get("name")
        if emsg:
            blocks.append(ContentBlock(kind="text", text=f"⚠️ {err.get('name', 'error')}: {emsg}"))
    if not blocks:
        return None
    return ThreadItem(
        uuid=msg_id,
        role="assistant",
        type="message",
        timestamp=ts,
        model=msg.get("modelID"),
        blocks=blocks,
    )


def _load(conn: sqlite3.Connection, raw_id: str) -> Optional[tuple[dict, list[ThreadItem]]]:
    srow = conn.execute(
        "SELECT id, directory, title, version FROM session WHERE id=?", (raw_id,)
    ).fetchone()
    if srow is None:
        return None
    msgs = conn.execute(
        "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created, id",
        (raw_id,),
    ).fetchall()
    parts = _parts_for(conn, raw_id)
    items: list[ThreadItem] = []
    model = None
    for m in msgs:
        data = _loads(m["data"])
        item = _message_item(m["id"], data, parts.get(m["id"], []), _ts(m["time_created"]))
        if item is None:
            continue
        if item.role == "assistant" and item.model:
            model = item.model
        items.append(item)
    meta = {
        "directory": srow["directory"],
        "title": srow["title"],
        "version": srow["version"],
        "model": model,
    }
    return meta, items


def _title(meta: dict, items: list[ThreadItem]) -> tuple[str, str]:
    title = (meta.get("title") or "").strip()
    if title:
        return title[:140], "ai-title"
    for it in items:
        if it.role == "user" and it.text and it.text.strip():
            return it.text.strip().splitlines()[0][:120], "user"
    return "opencode session", "none"


# ---- provider ------------------------------------------------------------


class OpenCodeProvider(Provider):
    id = "opencode"
    display_name = "opencode"
    prefix = _PREFIX

    def iter_sessions(self) -> list[SessionSummary]:
        global _sessions_cache
        mtime = _db_mtime()
        if mtime is None:
            return []
        if _sessions_cache and _sessions_cache[0] == mtime:
            return _sessions_cache[1]
        conn = _connect()
        if conn is None:
            return []
        try:
            out = self._build_summaries(conn, mtime)
        finally:
            conn.close()
        _sessions_cache = (mtime, out)
        return out

    def _build_summaries(self, conn: sqlite3.Connection, mtime: float) -> list[SessionSummary]:
        # Root sessions only (children are subagent runs, parent_id set).
        sessions = conn.execute(
            "SELECT s.id, s.directory, s.title, s.version, s.time_updated, "
            "       p.worktree AS worktree "
            "FROM session s LEFT JOIN project p ON p.id = s.project_id "
            "WHERE s.parent_id IS NULL OR s.parent_id = '' "
            "ORDER BY s.time_updated DESC"
        ).fetchall()
        # Per-session aggregates in two grouped queries (no per-row JSON parse).
        counts = {
            r["session_id"]: (r["c"], r["b"] or 0)
            for r in conn.execute(
                "SELECT session_id, COUNT(*) c, SUM(LENGTH(data)) b FROM message GROUP BY session_id"
            ).fetchall()
        }
        part_bytes = {
            r["session_id"]: (r["b"] or 0)
            for r in conn.execute(
                "SELECT session_id, SUM(LENGTH(data)) b FROM part GROUP BY session_id"
            ).fetchall()
        }
        # Latest model + summed token usage per session, from one pass over the
        # (small) message envelopes.
        models, tokens = self._models_and_tokens(conn)
        out: list[SessionSummary] = []
        for s in sessions:
            sid = s["id"]
            n, mbytes = counts.get(sid, (0, 0))
            title = (s["title"] or "").strip() or "opencode session"
            out.append(
                SessionSummary(
                    session_id=_muse_id(sid),
                    provider="opencode",
                    project_cwd=s["directory"] or s["worktree"],
                    project_dir=s["worktree"] or "opencode",
                    title=title[:140],
                    title_source="ai-title" if (s["title"] or "").strip() else "none",
                    message_count=n,
                    total_tokens=tokens.get(sid, 0),
                    model=models.get(sid),
                    mtime=_ts(s["time_updated"]) or datetime.fromtimestamp(mtime, tz=timezone.utc),
                    size_bytes=mbytes + part_bytes.get(sid, 0),
                    state="stopped",  # opencode sessions aren't live-tracked by muse
                )
            )
        return out

    def _models_and_tokens(
        self, conn: sqlite3.Connection
    ) -> tuple[dict[str, str], dict[str, int]]:
        models: dict[str, str] = {}
        tokens: dict[str, int] = {}
        # Newest-first: first assistant model per session wins; sum tokens.total
        # across every assistant turn (each carries its own usage).
        for r in conn.execute(
            "SELECT session_id, data FROM message ORDER BY time_created DESC"
        ).fetchall():
            sid = r["session_id"]
            d = _loads(r["data"])
            if d.get("role") != "assistant":
                continue
            if sid not in models and d.get("modelID"):
                models[sid] = d["modelID"]
            tok = d.get("tokens")
            if isinstance(tok, dict):
                cache = tok.get("cache") if isinstance(tok.get("cache"), dict) else {}
                # Real work: input + output + reasoning + cache writes; exclude
                # cache reads (consistent with the other providers).
                real = (
                    (tok.get("input", 0) or 0)
                    + (tok.get("output", 0) or 0)
                    + (tok.get("reasoning", 0) or 0)
                    + (cache.get("write", 0) or 0)
                )
                if real:
                    tokens[sid] = tokens.get(sid, 0) + real
        return models, tokens

    def load_thread(self, session_id: str) -> Optional[Thread]:
        conn = _connect()
        if conn is None:
            return None
        try:
            loaded = _load(conn, self.raw_id(session_id))
        finally:
            conn.close()
        if loaded is None:
            return None
        meta, items = loaded
        title, source = _title(meta, items)
        return Thread(
            session_id=_muse_id(session_id),
            provider="opencode",
            project_cwd=meta["directory"],
            version=meta["version"],
            title=title,
            title_source=source,  # type: ignore[arg-type]
            model=meta["model"],
            items=items,
        )

    def build_events(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[SessionEvent]]:
        conn = _connect()
        if conn is None:
            return None
        raw_id = self.raw_id(session_id)
        try:
            srow = conn.execute("SELECT id FROM session WHERE id=?", (raw_id,)).fetchone()
            if srow is None:
                return None
            msgs = conn.execute(
                "SELECT id, time_created, data FROM message WHERE session_id=? "
                "ORDER BY time_created, id",
                (raw_id,),
            ).fetchall()
            parts = _parts_for(conn, raw_id)
        finally:
            conn.close()

        events: list[SessionEvent] = []
        n = 0

        def add(**kw):
            nonlocal n
            events.append(SessionEvent(index=n, **kw))
            n += 1

        for m in msgs:
            msg_id = m["id"]
            data = _loads(m["data"])
            role = data.get("role", "assistant")
            ts = _ts(data.get("time", {}).get("created")) or _ts(m["time_created"])
            if role == "user":
                text = "\n".join(
                    p.get("text", "")
                    for p in parts.get(msg_id, [])
                    if p.get("type") == "text" and p.get("text")
                )
                if text.strip():
                    add(kind="user", type="message", role="user", timestamp=ts,
                        anchor_uuid=msg_id, label=_first_line(text), detail=text)
                continue
            # assistant turn → expand parts in order
            for p in parts.get(msg_id, []):
                pt = p.get("type")
                if pt == "reasoning" and p.get("text", "").strip():
                    add(kind="thinking", type="reasoning", role="assistant", timestamp=ts,
                        anchor_uuid=msg_id, label="thinking", detail=p["text"])
                elif pt == "text" and p.get("text", "").strip():
                    add(kind="assistant_text", type="message", role="assistant", timestamp=ts,
                        anchor_uuid=msg_id, label=_first_line(p["text"]), detail=p["text"])
                elif pt == "tool":
                    state = p.get("state") or {}
                    name = p.get("tool", "tool")
                    call_id = p.get("callID") or msg_id
                    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
                    status = state.get("status")
                    stime = state.get("time") or {}
                    dur = None
                    if stime.get("start") and stime.get("end"):
                        dur = int(stime["end"] - stime["start"])
                    add(kind="tool_call", type="tool", role="assistant", timestamp=ts,
                        anchor_uuid=msg_id, tool_use_id=call_id, tool_name=name,
                        label=f"{name}({_tool_summary(inp)})"[:140])
                    if status in ("completed", "error"):
                        out = state.get("output")
                        if out is None and status == "error":
                            out = state.get("error")
                        add(kind="tool_result", type="tool", role="user", timestamp=ts,
                            anchor_uuid=msg_id, tool_use_id=call_id,
                            status="error" if status == "error" else "ok",
                            is_error=status == "error", duration_ms=dur,
                            label=_first_line(_stringify(out), 100))
            # API / turn error surfaced as a system error event (feeds alerts)
            err = data.get("error")
            if isinstance(err, dict):
                emsg = (err.get("data") or {}).get("message") or err.get("name") or "error"
                add(kind="system", type="error", role="system", timestamp=ts,
                    anchor_uuid=msg_id, level="error", is_error=True,
                    label=f"{err.get('name', 'error')}: {emsg}"[:140], detail=_stringify(err))
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
                kind = _FILE_TOOLS.get(tu.name)
                if not kind:
                    continue
                path = tu.input.get("filePath") or tu.input.get("file_path") or tu.input.get("path")
                if not path:
                    continue
                fc = by_path.setdefault(path, FileChange(path=path))
                is_err = bool(tu.result and tu.result.is_error)
                fc.ops.append(FileOp(tool_use_id=tu.id, kind=kind, tool_name=tu.name,
                                     timestamp=item.timestamp, is_error=is_err))
                if kind == "read":
                    fc.read_count += 1
                elif kind == "write":
                    fc.write_count += 1
                else:
                    fc.edit_count += 1
                if is_err:
                    fc.error_count += 1
                if item.timestamp:
                    fc.first_ts = fc.first_ts or item.timestamp
                    fc.last_ts = item.timestamp
        return sorted(by_path.values(), key=lambda f: len(f.ops), reverse=True)

    def build_lineage(self, session_id: str) -> Optional[SessionLineage]:
        return SessionLineage(session_id=session_id)

    def search_docs(self) -> list[IndexDoc]:
        mtime = _db_mtime()
        if mtime is None:
            return []
        conn = _connect()
        if conn is None:
            return []
        try:
            # Per-session change-key = latest message time (falls back to the session's
            # own time_updated). Using the SHARED db mtime would reindex EVERY session on
            # any write; this reindexes only the session that actually changed.
            rows = conn.execute(
                "SELECT s.id, s.directory, "
                "COALESCE((SELECT MAX(m.time_created) FROM message m WHERE m.session_id=s.id), "
                "s.time_updated, 0) AS change_key "
                "FROM session s WHERE s.parent_id IS NULL OR s.parent_id = ''"
            ).fetchall()
            sessions = [(r["id"], r["directory"], r["change_key"]) for r in rows]
        finally:
            conn.close()
        docs: list[IndexDoc] = []
        for sid, directory, change_key in sessions:
            # Synthetic per-session "path" key (not a real file). SQLite-backed, so it
            # can't byte-offset → append_safe=False: a changed session is fully re-indexed.
            docs.append(
                IndexDoc(
                    path=f"opencode-db:{sid}",
                    mtime=float(change_key or 0),
                    session_id=_muse_id(sid),
                    project_dir=directory or "opencode",
                    rows_fn=partial(_search_rows, sid),
                    append_safe=False,
                )
            )
        return docs


def _search_rows(raw_id: str, offset: int, start_index: int) -> tuple[list[SearchRow], int, int]:
    """opencode is SQLite-backed (no byte offset); registered append_safe=False so this
    always does a full re-index of the one session. `offset`/`start_index` are unused."""
    conn = _connect()
    if conn is None:
        return [], 0, 0
    try:
        loaded = _load(conn, raw_id)
    finally:
        conn.close()
    if loaded is None:
        return [], 0, 0
    _meta, items = loaded
    rows: list[SearchRow] = []
    for it in items:
        ts = it.timestamp.isoformat() if it.timestamp else None
        if it.role == "user" and it.text and it.text.strip():
            rows.append((it.uuid, "user", ts, it.text[:4000]))
            continue
        for b in it.blocks:
            if b.kind in ("text", "thinking") and b.text and b.text.strip():
                rows.append((it.uuid, it.role, ts, b.text[:4000]))
            elif b.kind == "tool_use" and b.tool_use:
                tu = b.tool_use
                body = f"{tu.name} {_tool_summary(tu.input)}".strip()
                if body:
                    rows.append((it.uuid, "assistant", ts, body[:4000]))
    return rows, 0, 0
