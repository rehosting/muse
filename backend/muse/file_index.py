"""Cross-session file-activity index: which sessions touched which files.

muse's own writable index (same DB file as annotations/search, separate tables).
Inverts the per-session `build_file_changes` view into a queryable table so
"every session that ever touched core_config.yaml" is one indexed lookup.

Sync is summary-driven: only sessions whose mtime changed get re-extracted
(via the provider's existing build_file_changes — one source of truth for op
extraction across all providers), and a per-session rate limit keeps a live,
constantly-appending transcript from being re-parsed on every tick. Rows keep
the tool_use_id as the anchor, which the viewer's ?focus= param accepts.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import db
from .models import FileChange, SessionSummary

# A live session's file activity is re-extracted at most this often (the extract
# loads the full thread, so this bounds re-parse churn for big live sessions).
_MIN_REINDEX_SECONDS = 300.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_index_sessions (
    session_id TEXT PRIMARY KEY,
    mtime      REAL,
    indexed_at REAL
);
CREATE TABLE IF NOT EXISTS file_activity (
    session_id  TEXT NOT NULL,
    provider    TEXT NOT NULL,
    project_cwd TEXT,
    file_path   TEXT NOT NULL,
    basename    TEXT NOT NULL,
    op          TEXT NOT NULL,        -- read | edit | write
    tool_use_id TEXT,                 -- viewer anchor (?focus=)
    is_error    INTEGER NOT NULL DEFAULT 0,
    ts          TEXT
);
CREATE INDEX IF NOT EXISTS fa_path_idx ON file_activity(file_path);
CREATE INDEX IF NOT EXISTS fa_base_idx ON file_activity(basename);
CREATE INDEX IF NOT EXISTS fa_session_idx ON file_activity(session_id);
"""

ChangesFn = Callable[[str], Optional[list[FileChange]]]


class FileIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = db.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- indexing -----------------------------------------------------------
    def sync(self, summaries: list[SessionSummary], changes_fn: ChangesFn) -> int:
        """Re-extract file activity for sessions whose mtime advanced (rate-limited
        per session) and prune vanished sessions. Returns sessions re-indexed."""
        now = time.time()  # wall clock: indexed_at persists across restarts

        def _do() -> int:
            with self._lock:
                seen = {
                    r["session_id"]: r
                    for r in self._conn.execute(
                        "SELECT session_id, mtime, indexed_at FROM file_index_sessions"
                    ).fetchall()
                }
            todo: list[SessionSummary] = []
            for s in summaries:
                prev = seen.get(s.session_id)
                mtime = s.mtime.timestamp()
                if prev is not None and prev["mtime"] == mtime:
                    continue
                if (
                    prev is not None
                    and prev["indexed_at"] is not None
                    and (now - prev["indexed_at"]) < _MIN_REINDEX_SECONDS
                ):
                    continue  # live session — don't re-parse on every tick
                todo.append(s)

            synced = 0
            for s in todo:
                # Extraction happens OUTSIDE the lock (it loads the full thread).
                changes = changes_fn(s.session_id)
                if changes is None:
                    continue
                with self._lock:
                    try:
                        self._conn.execute(
                            "DELETE FROM file_activity WHERE session_id=?", (s.session_id,)
                        )
                        for fc in changes:
                            base = fc.path.rsplit("/", 1)[-1]
                            for op in fc.ops:
                                self._conn.execute(
                                    "INSERT INTO file_activity(session_id, provider, "
                                    "project_cwd, file_path, basename, op, tool_use_id, "
                                    "is_error, ts) VALUES(?,?,?,?,?,?,?,?,?)",
                                    (s.session_id, s.provider, s.project_cwd, fc.path,
                                     base, op.kind, op.tool_use_id, int(op.is_error),
                                     op.timestamp.isoformat() if op.timestamp else None),
                                )
                        self._conn.execute(
                            "INSERT INTO file_index_sessions(session_id, mtime, indexed_at) "
                            "VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                            "mtime=excluded.mtime, indexed_at=excluded.indexed_at",
                            (s.session_id, s.mtime.timestamp(), now),
                        )
                        self._conn.commit()
                        synced += 1
                    except sqlite3.OperationalError:
                        self._conn.rollback()
                        raise

            present = {s.session_id for s in summaries}
            with self._lock:
                try:
                    for sid in seen:
                        if sid not in present:
                            self._conn.execute(
                                "DELETE FROM file_activity WHERE session_id=?", (sid,)
                            )
                            self._conn.execute(
                                "DELETE FROM file_index_sessions WHERE session_id=?", (sid,)
                            )
                    self._conn.commit()
                except sqlite3.OperationalError:
                    self._conn.rollback()
                    raise
            return synced

        try:
            return db.retry_locked(_do)
        except sqlite3.OperationalError:
            return 0  # give up gracefully; next tick retries

    # --- querying -----------------------------------------------------------
    def search_files(self, q: str, limit: int = 50) -> list[dict]:
        """Distinct files matching a basename/path substring, with per-file totals."""
        like = f"%{q}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path, basename, COUNT(DISTINCT session_id) AS session_count, "
                "SUM(op='read') AS reads, SUM(op='edit') AS edits, "
                "SUM(op='write') AS writes, SUM(is_error) AS errors, MAX(ts) AS last_ts "
                "FROM file_activity WHERE basename LIKE ? OR file_path LIKE ? "
                "GROUP BY file_path ORDER BY last_ts DESC LIMIT ?",
                (like, like, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(r) for r in rows]

    def activity_for(self, file_path: str) -> list[dict]:
        """Every touch of one file, grouped per session (newest session first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, provider, project_cwd, op, tool_use_id, is_error, ts "
                "FROM file_activity WHERE file_path=? ORDER BY ts",
                (file_path,),
            ).fetchall()
        by_session: dict[str, dict] = {}
        for r in rows:
            g = by_session.setdefault(r["session_id"], {
                "session_id": r["session_id"], "provider": r["provider"],
                "project_cwd": r["project_cwd"], "ops": [],
                "reads": 0, "edits": 0, "writes": 0, "errors": 0,
                "first_ts": r["ts"], "last_ts": r["ts"],
            })
            g["ops"].append({"op": r["op"], "tool_use_id": r["tool_use_id"],
                             "is_error": bool(r["is_error"]), "ts": r["ts"]})
            key = {"read": "reads", "edit": "edits", "write": "writes"}.get(r["op"])
            if key:
                g[key] += 1
            g["errors"] += int(r["is_error"])
            if r["ts"]:
                g["last_ts"] = max(g["last_ts"] or r["ts"], r["ts"])
        return sorted(by_session.values(), key=lambda g: g["last_ts"] or "", reverse=True)

    def edited_files(self, session_id: str) -> set[str]:
        """Files a session edited or wrote (for related-session overlap scoring)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT file_path FROM file_activity "
                "WHERE session_id=? AND op IN ('edit','write')",
                (session_id,),
            ).fetchall()
        return {r["file_path"] for r in rows}

    def sessions_sharing_files(self, session_id: str) -> list[dict]:
        """Sessions that edited/wrote at least one file this session also touched."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT b.session_id AS other, b.file_path FROM file_activity a "
                "JOIN file_activity b ON a.file_path = b.file_path "
                "AND b.session_id != a.session_id AND b.op IN ('edit','write') "
                "WHERE a.session_id=? AND a.op IN ('edit','write') "
                "GROUP BY b.session_id, b.file_path",
                (session_id,),
            ).fetchall()
        by_other: dict[str, list[str]] = {}
        for r in rows:
            by_other.setdefault(r["other"], []).append(r["file_path"])
        return [
            {"session_id": sid, "shared_files": sorted(files)}
            for sid, files in by_other.items()
        ]

    def indexed_sessions(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM file_index_sessions"
            ).fetchone()
        return row["n"] if row else 0
