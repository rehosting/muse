"""Context packs: hand-off markdown a NEW session reads on launch.

A pack is muse-rendered markdown (re-entry brief + notes + file list from a
prior session, plus freeform extra) written to ~/.muse/packs/<id>.md — muse-
owned state, NEVER the project dir. The launcher's seed prompt names the
absolute path, and the new Claude Code session simply reads it.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import db
from .models import Pack

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pack (
    id                 TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    source_session_id  TEXT,
    body_md            TEXT NOT NULL,
    path               TEXT NOT NULL,
    created_at         TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PackStore:
    def __init__(self, path: Path, packs_dir: Path) -> None:
        self.path = path
        self.packs_dir = packs_dir
        path.parent.mkdir(parents=True, exist_ok=True)
        packs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = db.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _write(self, work: Callable):
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

    def create(
        self, title: str, body_md: str, source_session_id: Optional[str] = None
    ) -> Pack:
        pack_id = f"pk_{uuid.uuid4().hex[:12]}"
        file_path = self.packs_dir / f"{pack_id}.md"
        file_path.write_text(body_md, encoding="utf-8")
        now = _now()

        def work():
            self._conn.execute(
                "INSERT INTO pack(id, title, source_session_id, body_md, path, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (pack_id, title, source_session_id, body_md, str(file_path), now),
            )

        self._write(work)
        return Pack(
            id=pack_id, title=title, source_session_id=source_session_id,
            body_md=body_md, path=str(file_path), created_at=now,
        )

    def get(self, pack_id: str) -> Optional[Pack]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM pack WHERE id=?", (pack_id,)).fetchone()
        return Pack(**dict(r)) if r else None

    def list(self) -> list[Pack]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pack ORDER BY created_at DESC"
            ).fetchall()
        return [Pack(**dict(r)) for r in rows]

    def delete(self, pack_id: str) -> bool:
        pack = self.get(pack_id)

        def work():
            cur = self._conn.execute("DELETE FROM pack WHERE id=?", (pack_id,))
            return cur.rowcount > 0

        ok = self._write(work)
        if ok and pack:
            try:
                Path(pack.path).unlink(missing_ok=True)
            except OSError:
                pass
        return ok
