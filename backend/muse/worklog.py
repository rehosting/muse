"""SQLite-backed worklog notes: lightweight running notes about active work.

muse's own writable store (lives in ~/.muse/muse.db; never touches the read-only
transcript dirs). A note is much lighter than an Investigation: a timestamped
line of prose, optionally attached to a session and/or a specific step, grouped
by local day for the journal view. Kinds:
  - 'note'  — a plain worklog entry ("trying the WAL fix now")
  - 'next'  — an open loop / follow-up ("next: re-run with ASLR off")
  - 'brief' — an AI-written re-entry summary (via MCP add_note)
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import db
from .models import Note

_KINDS = ("note", "next", "brief")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS note (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    anchor_uuid TEXT,
    kind        TEXT NOT NULL DEFAULT 'note',
    author      TEXT NOT NULL DEFAULT 'user',
    body        TEXT NOT NULL,
    day         TEXT NOT NULL,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS note_session_idx ON note(session_id);
CREATE INDEX IF NOT EXISTS note_day_idx ON note(day);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _new_id() -> str:
    return f"note_{uuid.uuid4().hex[:12]}"


def _from_row(r: sqlite3.Row) -> Note:
    return Note(
        id=r["id"],
        session_id=r["session_id"],
        anchor_uuid=r["anchor_uuid"],
        kind=r["kind"],
        author=r["author"],
        body=r["body"],
        day=r["day"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


class WorklogStore:
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

    def _write(self, work: Callable):
        """Run a write under the lock with commit + lock-retry (other writers on
        the shared muse.db can briefly hold the write lock); rollback on failure."""
        def _do():
            with self._lock:
                try:
                    result = work()
                    self._conn.commit()
                    return result
                except sqlite3.OperationalError:
                    self._conn.rollback()
                    raise
        return db.retry_locked(_do)

    # --- CRUD ---------------------------------------------------------------
    def create_note(
        self,
        body: str,
        session_id: Optional[str] = None,
        anchor_uuid: Optional[str] = None,
        kind: str = "note",
        author: str = "user",
    ) -> Note:
        note_id = _new_id()
        now = _now()
        kind = kind if kind in _KINDS else "note"
        author = author if author in ("user", "ai") else "user"
        day = _today()

        def work():
            self._conn.execute(
                "INSERT INTO note(id, session_id, anchor_uuid, kind, author, body, day, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (note_id, session_id, anchor_uuid, kind, author, body, day, now, now),
            )

        self._write(work)
        return Note(
            id=note_id, session_id=session_id, anchor_uuid=anchor_uuid, kind=kind,
            author=author, body=body, day=day, created_at=now, updated_at=now,
        )

    def get_note(self, note_id: str) -> Optional[Note]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM note WHERE id=?", (note_id,)).fetchone()
        return _from_row(row) if row else None

    def list_notes(
        self,
        session_id: Optional[str] = None,
        day: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 200,
    ) -> list[Note]:
        """Notes filtered by session and/or day, newest first."""
        clauses, params = [], []
        if session_id is not None:
            clauses.append("session_id=?")
            params.append(session_id)
        if day is not None:
            clauses.append("day=?")
            params.append(day)
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM note {where} ORDER BY created_at DESC, id LIMIT ?",  # noqa: S608
                (*params, max(1, min(limit, 1000))),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def sessions_with_open_next(self) -> set[str]:
        """Session ids that carry an unresolved 'next' note (open loops)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT session_id FROM note "
                "WHERE kind='next' AND session_id IS NOT NULL"
            ).fetchall()
        return {r["session_id"] for r in rows}

    def update_note(
        self,
        note_id: str,
        body: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> Optional[Note]:
        def work():
            row = self._conn.execute("SELECT * FROM note WHERE id=?", (note_id,)).fetchone()
            if row is None:
                return False
            new_body = body if body is not None else row["body"]
            new_kind = kind if kind in _KINDS else row["kind"]
            self._conn.execute(
                "UPDATE note SET body=?, kind=?, updated_at=? WHERE id=?",
                (new_body, new_kind, _now(), note_id),
            )
            return True

        return self.get_note(note_id) if self._write(work) else None

    def delete_note(self, note_id: str) -> bool:
        def work():
            cur = self._conn.execute("DELETE FROM note WHERE id=?", (note_id,))
            return cur.rowcount > 0

        return self._write(work)
