"""SQLite-backed Investigations: AI/user-authored markup documents.

muse's own writable store (lives in ~/.muse/muse.db; never touches the read-only
transcript dirs). An Investigation is prose + references into real sessions; the
references are indexed by session_id so a session can show its backlinks ("which
investigations cite me"). The user's Claude Code creates/reads these over MCP.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import db
from .models import (
    Investigation,
    InvestigationRef,
    InvestigationSummary,
    SessionBacklink,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS investigation (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT 'ai',
    status     TEXT NOT NULL DEFAULT 'open',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS investigation_ref (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    anchor_uuid      TEXT,
    label            TEXT NOT NULL DEFAULT '',
    comment          TEXT NOT NULL DEFAULT '',
    created_at       TEXT
);
CREATE INDEX IF NOT EXISTS inv_ref_inv_idx ON investigation_ref(investigation_id);
CREATE INDEX IF NOT EXISTS inv_ref_session_idx ON investigation_ref(session_id);
"""

# Additive migrations (mirrors search.py): a retro is just an investigation with a
# kind tag — same prose+refs shape, so it reuses the whole store/router/UI stack.
_MIGRATIONS = [
    "ALTER TABLE investigation ADD COLUMN kind TEXT NOT NULL DEFAULT 'investigation'",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class InvestigationStore:
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
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _write(self, work: Callable):
        """Run a write under the lock with commit + lock-retry (other writers on the
        shared muse.db can briefly hold the write lock); rollback on failure."""
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

    # --- helpers ------------------------------------------------------------
    def _ref_from_row(self, r: sqlite3.Row) -> InvestigationRef:
        return InvestigationRef(
            id=r["id"],
            session_id=r["session_id"],
            anchor_uuid=r["anchor_uuid"],
            label=r["label"] or "",
            comment=r["comment"] or "",
            created_at=r["created_at"],
        )

    def _refs_for(self, investigation_id: str) -> list[InvestigationRef]:
        rows = self._conn.execute(
            "SELECT * FROM investigation_ref WHERE investigation_id=? ORDER BY created_at, id",
            (investigation_id,),
        ).fetchall()
        return [self._ref_from_row(r) for r in rows]

    def _insert_ref(
        self,
        investigation_id: str,
        session_id: str,
        anchor_uuid: Optional[str],
        label: str,
        comment: str,
    ) -> InvestigationRef:
        ref_id = _new_id("ref")
        now = _now()
        self._conn.execute(
            "INSERT INTO investigation_ref"
            "(id, investigation_id, session_id, anchor_uuid, label, comment, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (ref_id, investigation_id, session_id, anchor_uuid, label or "", comment or "", now),
        )
        return InvestigationRef(
            id=ref_id,
            session_id=session_id,
            anchor_uuid=anchor_uuid,
            label=label or "",
            comment=comment or "",
            created_at=now,
        )

    # --- CRUD ---------------------------------------------------------------
    def create_investigation(
        self,
        title: str,
        body: str = "",
        author: str = "ai",
        status: str = "open",
        refs: Optional[list[dict]] = None,
        kind: str = "investigation",
    ) -> Investigation:
        inv_id = _new_id("inv")
        now = _now()
        author = author if author in ("ai", "user") else "ai"
        kind = kind if kind in ("investigation", "retro") else "investigation"

        def work():
            self._conn.execute(
                "INSERT INTO investigation(id, title, body, author, status, kind, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (inv_id, title.strip() or "Untitled investigation", body or "", author,
                 status or "open", kind, now, now),
            )
            return [
                self._insert_ref(
                    inv_id, r["session_id"], r.get("anchor_uuid"),
                    r.get("label", ""), r.get("comment", ""),
                )
                for r in (refs or [])
                if r.get("session_id")
            ]

        created_refs = self._write(work)
        return Investigation(
            id=inv_id, title=title.strip() or "Untitled investigation", body=body or "",
            author=author, status=status or "open", kind=kind, refs=created_refs,
            created_at=now, updated_at=now,
        )

    def get_investigation(self, investigation_id: str) -> Optional[Investigation]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM investigation WHERE id=?", (investigation_id,)
            ).fetchone()
            if row is None:
                return None
            refs = self._refs_for(investigation_id)
        return Investigation(
            id=row["id"], title=row["title"], body=row["body"] or "",
            author=row["author"], status=row["status"], kind=row["kind"], refs=refs,
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list_investigations(self) -> list[InvestigationSummary]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT i.*, "
                "(SELECT COUNT(*) FROM investigation_ref r WHERE r.investigation_id=i.id) AS ref_count "
                "FROM investigation i ORDER BY i.updated_at DESC, i.id"
            ).fetchall()
        return [
            InvestigationSummary(
                id=r["id"], title=r["title"], author=r["author"], status=r["status"],
                kind=r["kind"], ref_count=r["ref_count"],
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def update_investigation(
        self,
        investigation_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        status: Optional[str] = None,
        append_body: Optional[str] = None,
    ) -> Optional[Investigation]:
        def work():
            row = self._conn.execute(
                "SELECT * FROM investigation WHERE id=?", (investigation_id,)
            ).fetchone()
            if row is None:
                return False
            new_title = title.strip() if title is not None and title.strip() else row["title"]
            new_body = body if body is not None else row["body"]
            if append_body:
                sep = "\n\n" if new_body else ""
                new_body = f"{new_body}{sep}{append_body}"
            new_status = status if status else row["status"]
            self._conn.execute(
                "UPDATE investigation SET title=?, body=?, status=?, updated_at=? WHERE id=?",
                (new_title, new_body, new_status, _now(), investigation_id),
            )
            return True

        return self.get_investigation(investigation_id) if self._write(work) else None

    def delete_investigation(self, investigation_id: str) -> bool:
        def work():
            cur = self._conn.execute(
                "DELETE FROM investigation WHERE id=?", (investigation_id,)
            )
            self._conn.execute(
                "DELETE FROM investigation_ref WHERE investigation_id=?", (investigation_id,)
            )
            return cur.rowcount > 0

        return self._write(work)

    # --- references ---------------------------------------------------------
    def add_reference(
        self,
        investigation_id: str,
        session_id: str,
        anchor_uuid: Optional[str] = None,
        label: str = "",
        comment: str = "",
    ) -> Optional[InvestigationRef]:
        def work():
            exists = self._conn.execute(
                "SELECT 1 FROM investigation WHERE id=?", (investigation_id,)
            ).fetchone()
            if exists is None:
                return None
            ref = self._insert_ref(investigation_id, session_id, anchor_uuid, label, comment)
            self._conn.execute(
                "UPDATE investigation SET updated_at=? WHERE id=?", (_now(), investigation_id)
            )
            return ref

        return self._write(work)

    def remove_reference(self, ref_id: str) -> bool:
        def work():
            cur = self._conn.execute("DELETE FROM investigation_ref WHERE id=?", (ref_id,))
            return cur.rowcount > 0

        return self._write(work)

    def get_session_references(self, session_id: str) -> list[SessionBacklink]:
        """Backlinks: investigations that reference this session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.*, i.title AS inv_title, i.author AS inv_author, i.kind AS inv_kind "
                "FROM investigation_ref r JOIN investigation i ON i.id = r.investigation_id "
                "WHERE r.session_id=? ORDER BY r.created_at, r.id",
                (session_id,),
            ).fetchall()
        return [
            SessionBacklink(
                investigation_id=r["investigation_id"],
                investigation_title=r["inv_title"],
                author=r["inv_author"],
                kind=r["inv_kind"],
                ref=self._ref_from_row(r),
            )
            for r in rows
        ]
