"""Cross-session full-text search backed by SQLite FTS5.

muse's own writable index (lives in the same DB file as annotations, separate
tables). Transcripts stay strictly read-only — we only read them to populate the
index. The index is **provider-driven**: each provider yields `IndexDoc`s
(path + mtime + lazy row extractor), and `sync()` re-indexes only files whose
mtime changed. One FTS row per message, keyed by uuid, so a hit deep-links to
that entry via the existing `?focus=<uuid>` viewer param.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import db

if TYPE_CHECKING:
    from .providers.base import IndexDoc  # noqa: F401

# Highlight markers wrapped around matched terms in snippets — control chars so
# they never collide with real transcript text; the frontend turns them into
# <mark> spans.
MARK_START = "\x02"
MARK_END = "\x03"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_files (
    path   TEXT PRIMARY KEY,
    mtime  REAL,
    size   INTEGER,
    offset INTEGER
);
"""

# Added for append-only indexing: how many objects have been indexed so far, so we
# can resume positional ids (codex `ci{i}`/gemini `gm{i}`) past this point. Additive
# migration — old rows get 0, which forces a one-time full re-index on next change.
_MIGRATIONS = [
    "ALTER TABLE search_files ADD COLUMN next_index INTEGER NOT NULL DEFAULT 0",
]

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    path UNINDEXED,
    session_id UNINDEXED,
    project_dir UNINDEXED,
    uuid UNINDEXED,
    role UNINDEXED,
    ts UNINDEXED,
    body,
    tokenize = 'porter unicode61'
);
"""


def _build_match_query(q: str) -> Optional[str]:
    """Forgiving FTS5 query: AND of prefix-matched terms. None if no usable terms."""
    terms = []
    for raw in q.split():
        cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.").strip()
        if cleaned:
            terms.append(f'"{cleaned}"*')
    return " ".join(terms) if terms else None


class SearchIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = db.connect(path)
        self._conn.executescript(_SCHEMA)
        for ddl in _MIGRATIONS:
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
        self.available = True
        try:
            self._conn.executescript(_FTS_SCHEMA)
        except sqlite3.OperationalError:
            # SQLite built without FTS5 — degrade gracefully.
            self.available = False
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- indexing -----------------------------------------------------------
    def sync(self, docs: list["IndexDoc"]) -> int:
        """Index only files whose mtime changed; append just the new rows of an
        append-only transcript (no full re-tokenize) and prune vanished files.
        Returns the number of rows indexed."""
        if not self.available:
            return 0

        def _do() -> int:
            added = 0
            with self._lock:
                try:
                    seen = {
                        r["path"]: r
                        for r in self._conn.execute(
                            "SELECT path, mtime, size, offset, next_index FROM search_files"
                        ).fetchall()
                    }
                    present: set[str] = set()
                    for doc in docs:
                        present.add(doc.path)
                        prev = seen.get(doc.path)
                        if prev is not None and prev["mtime"] == doc.mtime:
                            continue  # unchanged

                        # Append only when we have prior append-only state and the file
                        # grew (or held). Otherwise (new file, truncation, opencode
                        # SQLite doc, or pre-migration row with next_index==0) full re-index.
                        can_append = (
                            doc.append_safe
                            and prev is not None
                            and prev["next_index"] > 0
                            and doc.size >= (prev["size"] or 0)
                        )
                        if can_append:
                            start_off, start_idx = prev["offset"] or 0, prev["next_index"]
                        else:
                            self._conn.execute("DELETE FROM search_fts WHERE path=?", (doc.path,))
                            start_off, start_idx = 0, 0

                        rows, new_off, new_idx = doc.rows_fn(start_off, start_idx)
                        for uuid, role, ts, body in rows:
                            if not body:
                                continue
                            self._conn.execute(
                                "INSERT INTO search_fts(path, session_id, project_dir, uuid, "
                                "role, ts, body) VALUES(?,?,?,?,?,?,?)",
                                (doc.path, doc.session_id, doc.project_dir, uuid, role, ts, body),
                            )
                            added += 1
                        self._conn.execute(
                            "INSERT INTO search_files(path, mtime, size, offset, next_index) "
                            "VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                            "mtime=excluded.mtime, size=excluded.size, offset=excluded.offset, "
                            "next_index=excluded.next_index",
                            (doc.path, doc.mtime, doc.size, new_off, new_idx),
                        )
                    for path in seen:  # prune files that disappeared
                        if path not in present:
                            self._conn.execute("DELETE FROM search_fts WHERE path=?", (path,))
                            self._conn.execute("DELETE FROM search_files WHERE path=?", (path,))
                    self._conn.commit()
                except sqlite3.OperationalError:
                    self._conn.rollback()
                    raise
            return added

        try:
            return db.retry_locked(_do)
        except sqlite3.OperationalError:
            return 0  # give up gracefully; next tick retries

    # --- querying -----------------------------------------------------------
    def search(self, q: str, limit: int = 30) -> list[dict]:
        if not self.available:
            return []
        match = _build_match_query(q)
        if not match:
            return []
        sql = (
            "SELECT session_id, project_dir, uuid, role, ts, "
            f"snippet(search_fts, 6, '{MARK_START}', '{MARK_END}', '…', 12) AS snippet "
            "FROM search_fts WHERE search_fts MATCH ? ORDER BY rank LIMIT ?"
        )
        def _do():
            with self._lock:
                return self._conn.execute(sql, (match, limit)).fetchall()

        try:
            rows = db.retry_locked(_do)
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    def indexed_sessions(self) -> int:
        if not self.available:
            return 0
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM search_files").fetchone()
        return row["n"] if row else 0
