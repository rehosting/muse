"""SQLite persistence for autopilot config, the armed flag, and an action log.

Uses muse's own DB (never ~/.claude).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import db
from ..models import AutopilotConfig, AutopilotLogEntry, QueuedReply

_SCHEMA = """
CREATE TABLE IF NOT EXISTS autopilot_config (
    session_id    TEXT PRIMARY KEY,
    enabled       INTEGER NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    max_sends     INTEGER NOT NULL DEFAULT 5,
    sent_count    INTEGER NOT NULL DEFAULT 0,
    interval_seconds INTEGER NOT NULL DEFAULT 30,
    last_sent_at  TEXT,
    last_seen_updated_at TEXT,
    context_threshold_pct INTEGER NOT NULL DEFAULT 80,
    context_action TEXT NOT NULL DEFAULT 'compact',
    context_message TEXT NOT NULL DEFAULT '',
    backoff_seconds INTEGER NOT NULL DEFAULT 900,
    backoff_until TEXT,
    idle_mode TEXT NOT NULL DEFAULT 'message'
);
CREATE TABLE IF NOT EXISTS autopilot_kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS autopilot_log (
    ts TEXT, session_id TEXT, action TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS reply_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | cancelled | failed
    mode       TEXT NOT NULL DEFAULT 'turn',     -- turn (own turn) | append (glue to previous)
    sent_at    TEXT,
    error      TEXT
);
CREATE INDEX IF NOT EXISTS reply_queue_pending
    ON reply_queue(session_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS tmux_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,   -- when captured (ISO)
    sig           TEXT NOT NULL,   -- topology signature (change detection)
    snapshot_json TEXT NOT NULL    -- serialized {ts, groups:[...]}
);
"""

# Columns added after the initial release (migrate existing DBs).
_MIGRATIONS = [
    "ALTER TABLE autopilot_config ADD COLUMN context_threshold_pct INTEGER NOT NULL DEFAULT 80",
    "ALTER TABLE autopilot_config ADD COLUMN context_action TEXT NOT NULL DEFAULT 'compact'",
    "ALTER TABLE autopilot_config ADD COLUMN context_message TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE autopilot_config ADD COLUMN backoff_seconds INTEGER NOT NULL DEFAULT 900",
    "ALTER TABLE autopilot_config ADD COLUMN backoff_until TEXT",
    "ALTER TABLE autopilot_config ADD COLUMN idle_mode TEXT NOT NULL DEFAULT 'message'",
    # AI idle mode: the in-flight draft job + the updated_at snapshot it was
    # requested against (Phase B discards the draft if the session moved on).
    "ALTER TABLE autopilot_config ADD COLUMN ai_pending_job_id TEXT",
    "ALTER TABLE autopilot_config ADD COLUMN ai_requested_updated_at TEXT",
    "ALTER TABLE reply_queue ADD COLUMN mode TEXT NOT NULL DEFAULT 'turn'",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class AutopilotStore:
    def __init__(self, path: Path) -> None:
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

    # --- armed flag ---------------------------------------------------------
    def is_armed(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM autopilot_kv WHERE key='armed'"
            ).fetchone()
        return bool(row and row["value"] == "1")

    def set_armed(self, armed: bool) -> None:
        self._set_kv("armed", "1" if armed else "0")

    def _get_kv(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM autopilot_kv WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def _set_kv(self, key: str, value: str) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO autopilot_kv(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
                self._conn.commit()

        db.retry_locked(_do)

    # --- tmux layout snapshots (session restore) ---------------------------
    def save_snapshot(self, snapshot_json: str, sig: str) -> None:
        """Append a topology snapshot and prune to the last ~20 rows (a small history
        ring so a good last-known-good survives even if a newer capture is thin)."""

        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO tmux_snapshots(ts, sig, snapshot_json) VALUES(?,?,?)",
                    (_now(), sig, snapshot_json),
                )
                self._conn.execute(
                    "DELETE FROM tmux_snapshots WHERE id NOT IN "
                    "(SELECT id FROM tmux_snapshots ORDER BY id DESC LIMIT 20)"
                )
                self._conn.commit()

        db.retry_locked(_do)

    def latest_snapshot(self) -> Optional[tuple[str, str]]:
        """The most recent (sig, snapshot_json), or None if nothing captured yet."""
        with self._lock:
            row = self._conn.execute(
                "SELECT sig, snapshot_json FROM tmux_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return (row["sig"], row["snapshot_json"]) if row else None

    # --- active-hours schedule ---------------------------------------------
    def get_schedule(self) -> tuple[bool, int, int]:
        enabled = self._get_kv("sched_enabled") == "1"
        start = int(self._get_kv("sched_start") or 22)
        end = int(self._get_kv("sched_end") or 7)
        return enabled, start, end

    def set_schedule(self, enabled: bool, start_hour: int, end_hour: int) -> None:
        self._set_kv("sched_enabled", "1" if enabled else "0")
        self._set_kv("sched_start", str(start_hour))
        self._set_kv("sched_end", str(end_hour))

    # --- config -------------------------------------------------------------
    def all_configs(self) -> dict[str, AutopilotConfig]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM autopilot_config").fetchall()
        return {r["session_id"]: self._row_to_config(r) for r in rows}

    def get_config(self, sid: str) -> AutopilotConfig:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM autopilot_config WHERE session_id=?", (sid,)
            ).fetchone()
        return self._row_to_config(row) if row else AutopilotConfig(session_id=sid)

    def upsert_config(self, cfg: AutopilotConfig) -> AutopilotConfig:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO autopilot_config("
                    "session_id, enabled, idle_mode, message, max_sends, interval_seconds, "
                    "context_threshold_pct, context_action, context_message, backoff_seconds) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                    "enabled=excluded.enabled, idle_mode=excluded.idle_mode, message=excluded.message, "
                    "max_sends=excluded.max_sends, interval_seconds=excluded.interval_seconds, "
                    "context_threshold_pct=excluded.context_threshold_pct, "
                    "context_action=excluded.context_action, context_message=excluded.context_message, "
                    "backoff_seconds=excluded.backoff_seconds",
                    (
                        cfg.session_id,
                        1 if cfg.enabled else 0,
                        cfg.idle_mode,
                        cfg.message,
                        cfg.max_sends,
                        cfg.interval_seconds,
                        cfg.context_threshold_pct,
                        cfg.context_action,
                        cfg.context_message,
                        cfg.backoff_seconds,
                    ),
                )
                self._conn.commit()

        db.retry_locked(_do)
        return self.get_config(cfg.session_id)

    def set_enabled(self, sid: str, enabled: bool) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "UPDATE autopilot_config SET enabled=? WHERE session_id=?",
                    (1 if enabled else 0, sid),
                )
                self._conn.commit()

        db.retry_locked(_do)

    def set_backoff(self, sid: str, until: datetime) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "UPDATE autopilot_config SET backoff_until=? WHERE session_id=?",
                    (until.isoformat(), sid),
                )
                self._conn.commit()

        db.retry_locked(_do)

    def record_send(self, sid: str, seen_updated_at: Optional[datetime]) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "UPDATE autopilot_config SET sent_count=sent_count+1, last_sent_at=?, "
                    "last_seen_updated_at=? WHERE session_id=?",
                    (_now(), seen_updated_at.isoformat() if seen_updated_at else None, sid),
                )
                self._conn.commit()

        db.retry_locked(_do)

    def last_seen_updated_at(self, sid: str) -> Optional[datetime]:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seen_updated_at FROM autopilot_config WHERE session_id=?", (sid,)
            ).fetchone()
        return _dt(row["last_seen_updated_at"]) if row else None

    # --- ai idle-mode pending draft ------------------------------------------
    def set_ai_pending(self, sid: str, job_id: Optional[str],
                       requested_updated_at: Optional[datetime]) -> None:
        def _do() -> None:
            with self._lock:
                # The config row may not exist yet (Phase A can fire before the
                # user ever saved settings for this session).
                self._conn.execute(
                    "INSERT INTO autopilot_config(session_id) VALUES(?) "
                    "ON CONFLICT(session_id) DO NOTHING",
                    (sid,),
                )
                self._conn.execute(
                    "UPDATE autopilot_config SET ai_pending_job_id=?, "
                    "ai_requested_updated_at=? WHERE session_id=?",
                    (job_id,
                     requested_updated_at.isoformat() if requested_updated_at else None,
                     sid),
                )
                self._conn.commit()

        db.retry_locked(_do)

    def get_ai_pending(self, sid: str) -> tuple[Optional[str], Optional[datetime]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT ai_pending_job_id, ai_requested_updated_at "
                "FROM autopilot_config WHERE session_id=?",
                (sid,),
            ).fetchone()
        if row is None:
            return None, None
        return row["ai_pending_job_id"], _dt(row["ai_requested_updated_at"])

    # --- reply queue ----------------------------------------------------------
    # User-authored messages waiting for their session's turn to end. The
    # controller delivers them (it owns automated tmux writes); these methods are
    # just FIFO bookkeeping. Delivered/cancelled rows are kept briefly as history.

    def queue_add(self, sid: str, text: str, mode: str = "turn") -> QueuedReply:
        def _do() -> int:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO reply_queue(session_id, text, created_at, mode) "
                    "VALUES(?,?,?,?)",
                    (sid, text, _now(), mode),
                )
                self._conn.commit()
                return int(cur.lastrowid)

        qid = db.retry_locked(_do)
        return QueuedReply(id=qid, session_id=sid, text=text, mode=mode,
                           created_at=datetime.now(timezone.utc))

    def queue_for(self, sid: str, limit: int = 20) -> list[QueuedReply]:
        """This session's queue: all pending items (FIFO) + a few recent outcomes."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM (SELECT * FROM reply_queue WHERE session_id=? "
                "ORDER BY (status='pending') DESC, id DESC LIMIT ?) ORDER BY id ASC",
                (sid, limit),
            ).fetchall()
        return [self._row_to_queued(r) for r in rows]

    def queue_next(self, sid: str) -> Optional[QueuedReply]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reply_queue WHERE session_id=? AND status='pending' "
                "ORDER BY id ASC LIMIT 1",
                (sid,),
            ).fetchone()
        return self._row_to_queued(row) if row else None

    def queue_next_batch(self, sid: str) -> list[QueuedReply]:
        """What one delivery should type: the oldest pending item plus any items
        immediately after it whose mode is 'append' (they glue on rather than
        waiting for their own turn)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reply_queue WHERE session_id=? AND status='pending' "
                "ORDER BY id ASC",
                (sid,),
            ).fetchall()
        batch: list[QueuedReply] = []
        for r in rows:
            item = self._row_to_queued(r)
            if batch and item.mode != "append":
                break
            batch.append(item)
        return batch

    def queue_set_mode(self, sid: str, qid: int, mode: str) -> bool:
        """Flip a still-pending item between 'turn' and 'append'."""
        def _do() -> bool:
            with self._lock:
                cur = self._conn.execute(
                    "UPDATE reply_queue SET mode=? "
                    "WHERE id=? AND session_id=? AND status='pending'",
                    (mode, qid, sid),
                )
                self._conn.commit()
                return cur.rowcount > 0

        return db.retry_locked(_do)

    def queue_counts(self) -> dict[str, int]:
        """Pending count per session — one cheap query for board/panes badges."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, COUNT(*) AS n FROM reply_queue "
                "WHERE status='pending' GROUP BY session_id"
            ).fetchall()
        return {r["session_id"]: r["n"] for r in rows}

    def queue_cancel(self, sid: str, qid: int) -> bool:
        """Cancel one still-pending item. False if it's gone or already delivered."""
        def _do() -> bool:
            with self._lock:
                cur = self._conn.execute(
                    "UPDATE reply_queue SET status='cancelled' "
                    "WHERE id=? AND session_id=? AND status='pending'",
                    (qid, sid),
                )
                self._conn.commit()
                return cur.rowcount > 0

        return db.retry_locked(_do)

    def queue_mark(self, qid: int, status: str, error: Optional[str] = None) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "UPDATE reply_queue SET status=?, sent_at=?, error=? WHERE id=?",
                    (status, _now() if status == "sent" else None, error, qid),
                )
                # Bound history: keep the most recent 200 non-pending rows.
                self._conn.execute(
                    "DELETE FROM reply_queue WHERE status != 'pending' AND id NOT IN "
                    "(SELECT id FROM reply_queue WHERE status != 'pending' "
                    "ORDER BY id DESC LIMIT 200)"
                )
                self._conn.commit()

        db.retry_locked(_do)

    @staticmethod
    def _row_to_queued(r: sqlite3.Row) -> QueuedReply:
        keys = r.keys()
        return QueuedReply(
            id=r["id"],
            session_id=r["session_id"],
            text=r["text"],
            created_at=_dt(r["created_at"]),
            status=r["status"],
            mode=(r["mode"] if "mode" in keys else "turn") or "turn",
            sent_at=_dt(r["sent_at"]),
            error=r["error"],
        )

    # --- log ----------------------------------------------------------------
    def log(self, sid: str, action: str, detail: str = "") -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO autopilot_log(ts, session_id, action, detail) VALUES(?,?,?,?)",
                    (_now(), sid, action, detail),
                )
                self._conn.execute(
                    "DELETE FROM autopilot_log WHERE rowid NOT IN "
                    "(SELECT rowid FROM autopilot_log ORDER BY ts DESC LIMIT 200)"
                )
                self._conn.commit()

        db.retry_locked(_do)

    def recent_log_for(self, sid: str, limit: int = 20) -> list[AutopilotLogEntry]:
        """One session's send history (autopilot injections + manual board sends)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM autopilot_log WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                (sid, limit),
            ).fetchall()
        return [
            AutopilotLogEntry(
                ts=_dt(r["ts"]) or datetime.now(timezone.utc),
                session_id=r["session_id"],
                action=r["action"],
                detail=r["detail"] or "",
            )
            for r in rows
        ]

    def recent_log(self, limit: int = 50) -> list[AutopilotLogEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM autopilot_log ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            AutopilotLogEntry(
                ts=_dt(r["ts"]) or datetime.now(timezone.utc),
                session_id=r["session_id"],
                action=r["action"],
                detail=r["detail"] or "",
            )
            for r in rows
        ]

    @staticmethod
    def _row_to_config(r: sqlite3.Row) -> AutopilotConfig:
        keys = r.keys()
        return AutopilotConfig(
            session_id=r["session_id"],
            enabled=bool(r["enabled"]),
            idle_mode=(r["idle_mode"] if "idle_mode" in keys else "message") or "message",
            message=r["message"] or "",
            max_sends=r["max_sends"],
            sent_count=r["sent_count"],
            interval_seconds=r["interval_seconds"],
            last_sent_at=_dt(r["last_sent_at"]),
            context_threshold_pct=r["context_threshold_pct"] if "context_threshold_pct" in keys else 80,
            context_action=r["context_action"] if "context_action" in keys else "compact",
            context_message=(r["context_message"] if "context_message" in keys else "") or "",
            backoff_seconds=r["backoff_seconds"] if "backoff_seconds" in keys else 900,
            backoff_until=_dt(r["backoff_until"]) if "backoff_until" in keys else None,
        )
