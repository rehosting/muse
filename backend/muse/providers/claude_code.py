"""Claude Code provider — delegates to muse's existing (Claude-shaped) modules."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Optional

from .. import changes, discovery, eventlog, lineage, transcript, usage_cache
from ..config import get_settings
from ..incremental import new_objects
from ..models import FileChange, SessionEvent, SessionLineage, SessionSummary, Thread
from ..paths import find_session
from .base import IndexDoc, Provider, SearchRow

# Tool input keys worth indexing for search (filename/command/pattern), but not
# bulk content (file bodies, diffs) which would bloat the index.
_TOOL_KEYS = ("command", "file_path", "notebook_path", "pattern", "query", "url", "description")
_MAX_BODY = 4000


def _line_rows(path: Path, offset: int, start_index: int) -> tuple[list[SearchRow], int, int]:
    """Append-only: parse only the lines after `offset` and return (rows, new_offset,
    new_index). Rows key off the message's real uuid, so the index isn't needed for
    correctness here — it's just the running object count for the indexer's bookkeeping."""
    objs, new_offset = new_objects(path, offset)
    rows: list[SearchRow] = []
    for obj in objs:
        if obj.get("type") not in ("user", "assistant"):
            continue
        content = obj.get("message", {}).get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text"):
                    parts.append(b["text"])
                elif bt == "thinking" and b.get("thinking"):
                    parts.append(b["thinking"])
                elif bt == "tool_use":
                    inp = b.get("input") or {}
                    summary = " ".join(str(inp[k]) for k in _TOOL_KEYS if inp.get(k))
                    if b.get("name") or summary:
                        parts.append(f"{b.get('name', '')} {summary}".strip())
        body = "\n".join(p for p in parts if p).strip()
        if body:
            rows.append((obj.get("uuid"), str(obj.get("type")), obj.get("timestamp"), body[:_MAX_BODY]))
    return rows, new_offset, start_index + len(objs)


class ClaudeProvider(Provider):
    id = "claude"
    display_name = "Claude Code"
    prefix = ""  # default / backward-compatible (unprefixed ids)

    def iter_sessions(self) -> list[SessionSummary]:
        sessions = discovery.list_sessions()
        # Per-session token totals from the usage cache (mtime-incremental, so a
        # live session's count refreshes on its own without rescanning the rest).
        # Aggregate every event (main + subagents) onto its parent session id.
        # "Tokens used" = real work: non-cached input + cache creation + output.
        # Exclude cache_read (ev.cr) — re-reading the cached prefix every turn would
        # balloon the count into the billions and make sessions incomparable.
        totals: dict[str, int] = {}
        for ev in usage_cache.scan_all().events:
            totals[ev.sid] = totals.get(ev.sid, 0) + ev.input + ev.output + ev.cc
        for s in sessions:
            s.provider = "claude"
            s.total_tokens = totals.get(s.session_id, 0)
        return sessions

    def load_thread(self, session_id: str) -> Optional[Thread]:
        return transcript.load_thread(session_id)

    def load_subagent(self, session_id: str, agent_id: str) -> Optional[Thread]:
        return transcript.load_subagent_thread(session_id, agent_id)

    def build_events(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[SessionEvent]]:
        paths = find_session(session_id)
        if paths is None:
            return None
        jsonl = paths.subagent_jsonl(agent_id) if agent_id else paths.jsonl
        if not jsonl.is_file():
            return None
        return eventlog.build_events(jsonl, paths)

    def build_file_changes(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[FileChange]]:
        thread = (
            self.load_subagent(session_id, agent_id) if agent_id else self.load_thread(session_id)
        )
        if thread is None:
            return None
        return changes.build_file_changes(thread)

    def build_lineage(self, session_id: str) -> Optional[SessionLineage]:
        paths = find_session(session_id)
        if paths is None or not paths.jsonl.is_file():
            return None
        return lineage.build_lineage(paths.jsonl, session_id)

    def search_docs(self) -> list[IndexDoc]:
        docs: list[IndexDoc] = []
        projects = get_settings().projects_dir
        if not projects.is_dir():
            return docs
        for project_dir in projects.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl in project_dir.glob("*.jsonl"):
                try:
                    st = jsonl.stat()
                except OSError:
                    continue
                docs.append(
                    IndexDoc(
                        path=str(jsonl),
                        mtime=st.st_mtime,
                        session_id=jsonl.stem,
                        project_dir=project_dir.name,
                        rows_fn=partial(_line_rows, jsonl),
                        size=st.st_size,
                    )
                )
        return docs
