"""Provider adapter interface.

muse normalizes every supported tool's on-disk transcript format into the same
models (Thread / ThreadItem / ToolUse / SessionEvent / FileChange). Each provider
implements this interface; the registry routes by an opaque session-id prefix
(Claude ids are unprefixed for backward-compat; others use "<id>:" prefixes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from ..models import FileChange, SessionEvent, SessionLineage, SessionSummary, Thread

# rows_fn yields searchable rows: (uuid, role, ts_iso, text)
SearchRow = tuple[Optional[str], Optional[str], Optional[str], str]

# rows_fn(start_offset, start_index) -> (new_rows, new_offset, new_index).
# Append-only: it parses only the bytes after `start_offset` (the last indexed byte
# position) and numbers rows continuing from `start_index` (the count of objects
# already indexed — so positional ids like codex `ci{i}`/gemini `gm{i}` stay aligned
# with the conversation/timeline views). Returns the new byte offset + object count.
RowsFn = Callable[[int, int], tuple[list[SearchRow], int, int]]


@dataclass
class IndexDoc:
    """One indexable transcript file. `rows_fn` is lazy — the indexer only calls it
    when the file's mtime changed, so unchanged files cost nothing. JSONL transcripts
    are append-only (`append_safe=True`): the indexer resumes from the stored byte
    offset instead of re-tokenizing the whole file. `append_safe=False` (opencode's
    SQLite-backed docs) forces a full re-index of that doc whenever it changes."""

    path: str
    mtime: float
    session_id: str  # muse-facing id (already prefixed)
    project_dir: str
    rows_fn: RowsFn
    size: int = 0  # current file size; used to detect truncation (full re-index)
    append_safe: bool = True


class Provider(ABC):
    id: str = ""
    display_name: str = ""
    prefix: str = ""  # session-id prefix in muse ("" = Claude default)

    def owns(self, session_id: str) -> bool:
        return bool(self.prefix) and session_id.startswith(self.prefix)

    def raw_id(self, session_id: str) -> str:
        """Strip the muse prefix to the provider's native session id."""
        if self.prefix and session_id.startswith(self.prefix):
            return session_id[len(self.prefix):]
        return session_id

    @abstractmethod
    def iter_sessions(self) -> list[SessionSummary]: ...

    @abstractmethod
    def load_thread(self, session_id: str) -> Optional[Thread]: ...

    def load_subagent(self, session_id: str, agent_id: str) -> Optional[Thread]:
        return None  # most providers have no subagents

    @abstractmethod
    def build_events(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[SessionEvent]]: ...

    @abstractmethod
    def build_file_changes(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> Optional[list[FileChange]]: ...

    def build_lineage(self, session_id: str) -> Optional[SessionLineage]:
        return SessionLineage(session_id=session_id)

    @abstractmethod
    def search_docs(self) -> list[IndexDoc]: ...
