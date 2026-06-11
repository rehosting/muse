"""SQLite-backed annotations: session renames and message bookmarks with notes.

This is muse's own writable store. It never touches ~/.claude — the transcripts
stay strictly read-only. Annotations key off the (immutable) session_id and
message uuid from the transcripts.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import db
from .models import Annotations, Bookmark

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_meta (
    session_id   TEXT PRIMARY KEY,
    custom_title TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS bookmarks (
    session_id   TEXT NOT NULL,
    message_uuid TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT,
    updated_at   TEXT,
    PRIMARY KEY (session_id, message_uuid)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnnotationStore:
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

    # --- titles -------------------------------------------------------------
    def get_title(self, session_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT custom_title FROM session_meta WHERE session_id=?", (session_id,)
            ).fetchone()
        return row["custom_title"] if row and row["custom_title"] else None

    def all_titles(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, custom_title FROM session_meta WHERE custom_title IS NOT NULL"
            ).fetchall()
        return {r["session_id"]: r["custom_title"] for r in rows if r["custom_title"]}

    def set_title(self, session_id: str, title: Optional[str]) -> None:
        title = (title or "").strip()

        def _do() -> None:
            with self._lock:
                if not title:
                    self._conn.execute(
                        "DELETE FROM session_meta WHERE session_id=?", (session_id,)
                    )
                else:
                    self._conn.execute(
                        "INSERT INTO session_meta(session_id, custom_title, updated_at) "
                        "VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                        "custom_title=excluded.custom_title, updated_at=excluded.updated_at",
                        (session_id, title, _now()),
                    )
                self._conn.commit()

        db.retry_locked(_do)

    # --- bookmarks ----------------------------------------------------------
    def get_bookmarks(self, session_id: str) -> list[Bookmark]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT message_uuid, note, created_at, updated_at FROM bookmarks "
                "WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [
            Bookmark(
                message_uuid=r["message_uuid"],
                note=r["note"] or "",
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def upsert_bookmark(self, session_id: str, message_uuid: str, note: str) -> Bookmark:
        now = _now()

        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO bookmarks(session_id, message_uuid, note, created_at, updated_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(session_id, message_uuid) DO UPDATE SET "
                    "note=excluded.note, updated_at=excluded.updated_at",
                    (session_id, message_uuid, note or "", now, now),
                )
                self._conn.commit()

        db.retry_locked(_do)
        return Bookmark(message_uuid=message_uuid, note=note or "", created_at=now, updated_at=now)

    def delete_bookmark(self, session_id: str, message_uuid: str) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM bookmarks WHERE session_id=? AND message_uuid=?",
                    (session_id, message_uuid),
                )
                self._conn.commit()

        db.retry_locked(_do)

    def get_annotations(self, session_id: str) -> Annotations:
        return Annotations(
            session_id=session_id,
            custom_title=self.get_title(session_id),
            bookmarks=self.get_bookmarks(session_id),
        )
