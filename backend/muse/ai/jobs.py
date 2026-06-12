"""AI job queue: muse-owned table + a single-worker thread for `claude -p` jobs.

Jobs run 10–120s each, so they live on their own daemon thread — NOT the
AlertsWatcher tick (which must keep its ~5s cadence for alerts/index warms).
Concurrency is 1 by design: every job burns the same Max-plan 5h window the
user's real sessions use, so we never run jobs in parallel.

Store follows the WorklogStore pattern (shared ~/.muse/muse.db, threading.Lock,
db.retry_locked writes, additive migrations).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .. import db
from ..models import AIJob

KINDS = ("ask", "session_summary", "daily_digest", "weekly_retro")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_job (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'queued',
    result      TEXT,
    error       TEXT,
    model       TEXT,
    cost_usd    REAL,
    duration_ms INTEGER,
    created_at  TEXT,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ai_job_status_idx ON ai_job(status);
CREATE INDEX IF NOT EXISTS ai_job_kind_idx ON ai_job(kind, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"aij_{uuid.uuid4().hex[:12]}"


def _from_row(r: sqlite3.Row) -> AIJob:
    return AIJob(
        id=r["id"],
        kind=r["kind"],
        params=json.loads(r["params"] or "{}"),
        status=r["status"],
        result=json.loads(r["result"]) if r["result"] else None,
        error=r["error"],
        model=r["model"],
        cost_usd=r["cost_usd"],
        duration_ms=r["duration_ms"],
        created_at=r["created_at"],
        started_at=r["started_at"],
        finished_at=r["finished_at"],
    )


class AIJobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = db.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Jobs left 'running' by a crashed/restarted server can never finish.
        self._write(
            lambda: self._conn.execute(
                "UPDATE ai_job SET status='error', error='interrupted by restart', "
                "finished_at=? WHERE status='running'",
                (_now(),),
            )
        )

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

    # --- queue ----------------------------------------------------------------
    def enqueue(self, kind: str, params: dict, model: Optional[str] = None) -> AIJob:
        if kind not in KINDS:
            raise ValueError(f"unknown ai job kind: {kind!r}")
        job_id = _new_id()
        now = _now()

        def work():
            self._conn.execute(
                "INSERT INTO ai_job(id, kind, params, status, model, created_at) "
                "VALUES(?,?,?,'queued',?,?)",
                (job_id, kind, json.dumps(params), model, now),
            )

        self._write(work)
        return AIJob(
            id=job_id, kind=kind, params=params, status="queued",
            model=model, created_at=now,
        )

    def claim_next(self) -> Optional[AIJob]:
        """Atomically move the oldest queued job to running and return it."""

        def work():
            row = self._conn.execute(
                "SELECT * FROM ai_job WHERE status='queued' "
                "ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE ai_job SET status='running', started_at=? WHERE id=?",
                (_now(), row["id"]),
            )
            return row["id"]

        job_id = self._write(work)
        return self.get(job_id) if job_id else None

    def finish(
        self,
        job_id: str,
        result: Optional[dict] = None,
        error: Optional[str] = None,
        cost_usd: Optional[float] = None,
        duration_ms: Optional[int] = None,
        model: Optional[str] = None,
    ) -> None:
        status = "error" if error else "done"

        def work():
            self._conn.execute(
                "UPDATE ai_job SET status=?, result=?, error=?, cost_usd=?, "
                "duration_ms=?, model=COALESCE(?, model), finished_at=? WHERE id=?",
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    cost_usd,
                    duration_ms,
                    model,
                    _now(),
                    job_id,
                ),
            )

        self._write(work)

    def cancel(self, job_id: str) -> bool:
        """Cancel a QUEUED job. Running jobs are cancelled via the worker (which
        kills the subprocess; the run error then finishes the row)."""

        def work():
            cur = self._conn.execute(
                "UPDATE ai_job SET status='cancelled', finished_at=? "
                "WHERE id=? AND status='queued'",
                (_now(), job_id),
            )
            return cur.rowcount > 0

        return self._write(work)

    # --- reads ------------------------------------------------------------------
    def get(self, job_id: str) -> Optional[AIJob]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ai_job WHERE id=?", (job_id,)
            ).fetchone()
        return _from_row(row) if row else None

    def list_jobs(self, limit: int = 50, kind: Optional[str] = None) -> list[AIJob]:
        where = "WHERE kind=?" if kind else ""
        params: tuple = (kind,) if kind else ()
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM ai_job {where} ORDER BY created_at DESC, id LIMIT ?",  # noqa: S608
                (*params, max(1, min(limit, 500))),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def has_pending(self, kind: str, params_match: Optional[dict] = None) -> bool:
        """True if a queued/running job of this kind (optionally with matching
        params subset) exists — the scheduler's dedupe."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT params FROM ai_job WHERE kind=? AND status IN ('queued','running')",
                (kind,),
            ).fetchall()
        if not params_match:
            return bool(rows)
        for r in rows:
            try:
                p = json.loads(r["params"] or "{}")
            except json.JSONDecodeError:
                continue
            if all(p.get(k) == v for k, v in params_match.items()):
                return True
        return False

    def counts(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM ai_job GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def total_cost(self) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM ai_job"
            ).fetchone()
        return float(row["c"] or 0.0)

    def last_error(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT error FROM ai_job WHERE status='error' "
                "ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
        return row["error"] if row else None


class AIWorker(threading.Thread):
    """Single-consumer worker: pops queued jobs and runs them via `execute`.
    Kicked by enqueue (Event) with a 5s poll fallback; daemon so a hung
    subprocess can't block interpreter exit (lifespan stop() also kills it)."""

    def __init__(self, store: AIJobStore, execute: Callable[[AIJob], dict]) -> None:
        super().__init__(name="muse-ai-worker", daemon=True)
        self.store = store
        self.execute = execute
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._running_id: Optional[str] = None
        self.on_cancel_running: Optional[Callable[[], bool]] = None

    def kick(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.on_cancel_running:
            self.on_cancel_running()
        self._wake.set()
        self.join(timeout=timeout)

    def cancel_running(self, job_id: str) -> bool:
        if self._running_id != job_id or not self.on_cancel_running:
            return False
        return self.on_cancel_running()

    def run(self) -> None:  # pragma: no cover - thread loop; pieces tested directly
        while not self._stop.is_set():
            job = self.store.claim_next()
            if job is None:
                self._wake.wait(timeout=5.0)
                self._wake.clear()
                continue
            self.run_one(job)

    def run_one(self, job: AIJob) -> None:
        """Execute one claimed job to completion (split out for tests)."""
        self._running_id = job.id
        try:
            result = self.execute(job)
        except Exception as e:  # noqa: BLE001 - job errors must never kill the worker
            self.store.finish(job.id, error=str(e)[:1000])
        else:
            self.store.finish(
                job.id,
                result={k: v for k, v in result.items() if k != "_meta"},
                cost_usd=(result.get("_meta") or {}).get("cost_usd"),
                duration_ms=(result.get("_meta") or {}).get("duration_ms"),
                model=(result.get("_meta") or {}).get("model"),
            )
        finally:
            self._running_id = None
