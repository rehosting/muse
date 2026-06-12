"""Deterministic failure-pattern detection over a session's event timeline.

Three patterns, all computable without AI from `build_events` output:
  - retry loop: ≥3 consecutive tool calls, same tool, near-identical label,
    whose results errored — the agent banging its head against the same wall;
  - error spiral: a 10-result window where ≥50% of tool results are errors;
  - permission-denial cluster: ≥3 denial-shaped error texts.

A `session_health` snapshot table (in ~/.muse/muse.db) caches a per-session
score so the session list can badge health without scanning any transcript;
the snapshot refreshes in the alerts tick for sessions whose mtime advanced
(rate-limited, same philosophy as the search/file indexes).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import db
from .models import SessionEvent, SessionSummary

_RETRY_MIN = 3  # consecutive same-tool errored calls that count as a loop
_PREFIX_LEN = 40  # label prefix that must match for calls to count as "the same"
_SPIRAL_WINDOW = 10
_SPIRAL_RATIO = 0.5
_DENIAL_MIN = 3
_DENIAL_PHRASES = (
    "permission denied", "not allowed", "denied by", "requires approval",
    "user doesn't want", "rejected the tool", "operation not permitted",
)

_MIN_RECOMPUTE_SECONDS = 300.0  # live sessions re-score at most this often


def detect_patterns(events: list[SessionEvent]) -> dict:
    """Pure detection: returns counts + the concrete pattern instances (each with
    anchors so the UI can focus the exact steps)."""
    calls = [e for e in events if e.kind == "tool_call"]
    results = {e.tool_use_id: e for e in events if e.kind == "tool_result" and e.tool_use_id}

    def errored(call: SessionEvent) -> bool:
        r = results.get(call.tool_use_id)
        return bool(r and r.is_error)

    # --- retry loops ---------------------------------------------------------
    loops: list[dict] = []
    run: list[SessionEvent] = []

    def flush_run() -> None:
        if len(run) >= _RETRY_MIN and all(errored(c) for c in run):
            loops.append({
                "tool": run[0].tool_name,
                "label": run[0].label,
                "times": len(run),
                "anchors": [c.tool_use_id or c.anchor_uuid for c in run],
            })

    for c in calls:
        if (
            run
            and c.tool_name == run[-1].tool_name
            and (c.label or "")[:_PREFIX_LEN] == (run[-1].label or "")[:_PREFIX_LEN]
        ):
            run.append(c)
        else:
            flush_run()
            run = [c]
    flush_run()

    # --- error spirals ---------------------------------------------------------
    spirals: list[dict] = []
    res_seq = [e for e in events if e.kind == "tool_result"]
    i = 0
    while i + _SPIRAL_WINDOW <= len(res_seq):
        window = res_seq[i : i + _SPIRAL_WINDOW]
        errs = [e for e in window if e.is_error]
        if len(errs) >= _SPIRAL_WINDOW * _SPIRAL_RATIO:
            spirals.append({
                "start_anchor": window[0].tool_use_id or window[0].anchor_uuid,
                "errors": len(errs),
                "window": _SPIRAL_WINDOW,
            })
            i += _SPIRAL_WINDOW  # don't double-count overlapping windows
        else:
            i += 1

    # --- permission denials -----------------------------------------------------
    denials = [
        {"label": e.label, "anchor": e.tool_use_id or e.anchor_uuid}
        for e in events
        if (e.is_error or (e.kind == "system" and e.level == "error"))
        and any(p in (f"{e.label} {e.detail or ''}").lower() for p in _DENIAL_PHRASES)
    ]

    error_count = sum(
        1 for e in events if e.is_error or (e.kind == "system" and e.level == "error")
    )
    score = "ok"
    if loops or spirals or len(denials) >= _DENIAL_MIN:
        score = "bad"
    elif error_count >= 5 or denials:
        score = "warn"
    return {
        "score": score,
        "error_count": error_count,
        "retry_loops": loops,
        "error_spirals": spirals,
        "permission_denials": denials if len(denials) >= _DENIAL_MIN else [],
    }


class RollingHealth:
    """Incremental live-session health: the same three patterns as
    `detect_patterns`, but fed raw transcript objects one at a time (the board
    ticker's appended-bytes stream) so a RUNNING session can be flagged as
    stuck without ever re-parsing its transcript.

    Approximation note: retry runs are tracked over completed (call, result)
    pairs in arrival order, which matches the batch detector for the
    sequential tool use that retry loops actually exhibit."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, str]] = {}  # tool_use_id -> (tool, label40)
        self._run_key: Optional[tuple[str, str]] = None
        self._run_errors = 0
        self._worst_run: Optional[tuple[str, int]] = None  # (tool, times)
        self._recent_errors: list[bool] = []  # last N tool results
        self._spiral = False
        self._denials = 0
        self._error_count = 0

    def feed(self, obj: dict) -> None:
        typ = obj.get("type")
        if obj.get("isApiErrorMessage") or (typ == "system" and obj.get("level") == "error"):
            self._error_count += 1
            self._check_denial(str(obj.get("content") or ""))
            return
        if typ == "assistant":
            for b in (obj.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    inp = b.get("input") or {}
                    label = ""
                    for key in ("command", "file_path", "path", "pattern", "query",
                                "description", "prompt", "url"):
                        v = inp.get(key)
                        if isinstance(v, str):
                            label = v
                            break
                    self._pending[b["id"]] = (b.get("name") or "", label[:_PREFIX_LEN])
            return
        if typ != "user":
            return
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            is_err = bool(b.get("is_error"))
            self._recent_errors.append(is_err)
            if len(self._recent_errors) > _SPIRAL_WINDOW:
                self._recent_errors.pop(0)
            if (
                len(self._recent_errors) == _SPIRAL_WINDOW
                and sum(self._recent_errors) >= _SPIRAL_WINDOW * _SPIRAL_RATIO
            ):
                self._spiral = True
            text = b.get("content")
            if isinstance(text, list):
                text = " ".join(str(p.get("text", "")) for p in text if isinstance(p, dict))
            if is_err:
                self._error_count += 1
                self._check_denial(str(text or ""))
            # Retry-run tracking over completed pairs.
            info = self._pending.pop(b.get("tool_use_id") or "", None)
            if info is None:
                continue
            if is_err:
                if self._run_key == info:
                    self._run_errors += 1
                else:
                    self._run_key = info
                    self._run_errors = 1
                if self._run_errors >= _RETRY_MIN and (
                    self._worst_run is None or self._run_errors > self._worst_run[1]
                ):
                    self._worst_run = (info[0], self._run_errors)
            else:
                self._run_key = None
                self._run_errors = 0

    def _check_denial(self, text: str) -> None:
        if any(p in text.lower() for p in _DENIAL_PHRASES):
            self._denials += 1

    def score(self) -> tuple[str, list[str]]:
        """(ok|warn|bad, human-readable flags) — thresholds mirror detect_patterns."""
        flags: list[str] = []
        if self._run_errors >= _RETRY_MIN:  # live, still in the loop right now
            flags.append(f"stuck in retry loop ({self._run_key[0]} ×{self._run_errors})")
        elif self._worst_run is not None:
            flags.append(f"retry loop ({self._worst_run[0]} ×{self._worst_run[1]})")
        if self._spiral:
            flags.append("error spiral")
        if self._denials >= _DENIAL_MIN:
            flags.append(f"permission denials ×{self._denials}")
        if flags:
            return "bad", flags
        if self._error_count >= 5 or self._denials:
            return "warn", ([f"{self._error_count} errors"] if self._error_count else [])
        return "ok", []


_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_health (
    session_id  TEXT PRIMARY KEY,
    mtime       REAL,
    computed_at REAL,
    score       TEXT NOT NULL DEFAULT 'ok',
    error_count INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT
);
"""

EventsFn = Callable[[str], Optional[list[SessionEvent]]]


class HealthStore:
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

    def sync(self, summaries: list[SessionSummary], events_fn: EventsFn) -> int:
        """Re-score sessions whose mtime advanced (rate-limited per session) and
        prune vanished ones. Returns sessions re-scored."""
        now = time.time()

        def _do() -> int:
            with self._lock:
                seen = {
                    r["session_id"]: r
                    for r in self._conn.execute(
                        "SELECT session_id, mtime, computed_at FROM session_health"
                    ).fetchall()
                }
            todo = []
            for s in summaries:
                prev = seen.get(s.session_id)
                if prev is not None and prev["mtime"] == s.mtime.timestamp():
                    continue
                if (
                    prev is not None
                    and prev["computed_at"] is not None
                    and (now - prev["computed_at"]) < _MIN_RECOMPUTE_SECONDS
                ):
                    continue
                todo.append(s)

            scored = 0
            for s in todo:
                events = events_fn(s.session_id)  # outside the lock (parses the file)
                if events is None:
                    continue
                health = detect_patterns(events)
                with self._lock:
                    try:
                        self._conn.execute(
                            "INSERT INTO session_health(session_id, mtime, computed_at, "
                            "score, error_count, detail_json) VALUES(?,?,?,?,?,?) "
                            "ON CONFLICT(session_id) DO UPDATE SET mtime=excluded.mtime, "
                            "computed_at=excluded.computed_at, score=excluded.score, "
                            "error_count=excluded.error_count, detail_json=excluded.detail_json",
                            (s.session_id, s.mtime.timestamp(), now, health["score"],
                             health["error_count"], json.dumps(health)),
                        )
                        self._conn.commit()
                        scored += 1
                    except sqlite3.OperationalError:
                        self._conn.rollback()
                        raise

            present = {s.session_id for s in summaries}
            with self._lock:
                try:
                    for sid in seen:
                        if sid not in present:
                            self._conn.execute(
                                "DELETE FROM session_health WHERE session_id=?", (sid,)
                            )
                    self._conn.commit()
                except sqlite3.OperationalError:
                    self._conn.rollback()
                    raise
            return scored

        try:
            return db.retry_locked(_do)
        except sqlite3.OperationalError:
            return 0

    def scores(self) -> dict[str, str]:
        """session_id -> ok|warn|bad, for badging the session list in one read."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, score FROM session_health"
            ).fetchall()
        return {r["session_id"]: r["score"] for r in rows}

    def get(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT detail_json FROM session_health WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None or not row["detail_json"]:
            return None
        try:
            return json.loads(row["detail_json"])
        except ValueError:
            return None
