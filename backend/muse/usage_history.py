"""Persistent per-day usage history (muse-owned, ~/.muse/muse.db).

The live stats scan only knows what today's transcripts contain — delete a
transcript and its usage vanishes from every chart. This store rolls the scan
into durable per-LOCAL-day rows keyed (day, model, project_dir, agent_type),
so long-range trends survive transcript cleanup.

Seam rule (enforced by the READER, not the writer): history is authoritative
for days BEFORE today; the live scan is authoritative for today. The writer
upserts every day the current scan covers (including today, so the final
pre-midnight tick completes the day) and never deletes days it can't see.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import db
from .pricing import cost_usd
from .usage_cache import Event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_reset (
    resets_at   TEXT PRIMARY KEY,      -- UTC ISO; observed 5h-window reset boundary
    observed_at TEXT
);
CREATE TABLE IF NOT EXISTS limit_hit (
    observed_at TEXT PRIMARY KEY,      -- local ISO; when a usage-limit banner was seen
    kind        TEXT NOT NULL DEFAULT '5h',  -- '5h' | 'week'
    window_cost REAL NOT NULL          -- spend in that window at the moment of the hit
);
CREATE TABLE IF NOT EXISTS usage_daily (
    day         TEXT NOT NULL,         -- local YYYY-MM-DD
    model       TEXT NOT NULL,
    project_dir TEXT NOT NULL,
    agent_type  TEXT NOT NULL,         -- '' = main thread
    input       INTEGER NOT NULL DEFAULT 0,
    output      INTEGER NOT NULL DEFAULT 0,
    cc          INTEGER NOT NULL DEFAULT 0,
    cr          INTEGER NOT NULL DEFAULT 0,
    messages    INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model, project_dir, agent_type)
);
CREATE INDEX IF NOT EXISTS usage_daily_day_idx ON usage_daily(day);
"""


def _local_day(ts: Optional[datetime]) -> Optional[str]:
    return ts.astimezone().strftime("%Y-%m-%d") if ts else None


class UsageHistoryStore:
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

    # --- writer ---------------------------------------------------------------
    def roll(self, events: Iterable[Event]) -> int:
        """Roll usage events into per-day rows with MAX-merge upserts: a day's
        counters only grow while its transcripts exist, so MAX(stored, rescanned)
        keeps the fuller value even after some of the day's transcripts are
        deleted. Days/cells the scan no longer covers are never touched."""
        agg: dict[tuple, list] = {}
        for e in events:
            if e.total <= 0:
                continue
            day = _local_day(e.ts)
            if day is None:
                continue
            key = (day, e.model or "unknown", e.project_dir, e.agent_type or "")
            row = agg.setdefault(key, [0, 0, 0, 0, 0, 0.0])
            row[0] += e.input
            row[1] += e.output
            row[2] += e.cc
            row[3] += e.cr
            row[4] += 1
            row[5] += cost_usd(e.model, e.input, e.output, e.cc, e.cr)
        if not agg:
            return 0

        def work():
            self._conn.executemany(
                "INSERT INTO usage_daily(day, model, project_dir, agent_type, "
                "input, output, cc, cr, messages, cost_usd) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(day, model, project_dir, agent_type) DO UPDATE SET "
                "input=MAX(input, excluded.input), output=MAX(output, excluded.output), "
                "cc=MAX(cc, excluded.cc), cr=MAX(cr, excluded.cr), "
                "messages=MAX(messages, excluded.messages), "
                "cost_usd=MAX(cost_usd, excluded.cost_usd)",
                [(*k, *v) for k, v in agg.items()],
            )
            return len(agg)

        return self._write(work)

    # --- readers ---------------------------------------------------------------
    def rows(self, start_day: Optional[str], end_day: str) -> list[sqlite3.Row]:
        """Rows for start_day <= day <= end_day (start None = from the beginning)."""
        q = "SELECT * FROM usage_daily WHERE day <= ?"
        params: list = [end_day]
        if start_day:
            q += " AND day >= ?"
            params.append(start_day)
        with self._lock:
            return self._conn.execute(q, params).fetchall()

    # --- observed rate-limit resets (anchor the 5h window honestly) -------------
    def record_reset(self, resets_at: datetime) -> None:
        """Persist an observed usage-limit reset time (parsed by autopilot from
        the CLI's limit message). One observation anchors the whole day: later
        windows are resets_at + k*5h."""
        def work():
            self._conn.execute(
                "INSERT OR IGNORE INTO rate_reset(resets_at, observed_at) VALUES(?,?)",
                (resets_at.astimezone().isoformat(),
                 datetime.now().astimezone().isoformat()),
            )
        self._write(work)

    def latest_reset(self) -> Optional[datetime]:
        with self._lock:
            r = self._conn.execute("SELECT MAX(resets_at) AS m FROM rate_reset").fetchone()
        if not r or not r["m"]:
            return None
        try:
            return datetime.fromisoformat(r["m"])
        except ValueError:
            return None

    # --- observed limit ceilings -------------------------------------------------
    # Subscription plans have no published $ caps, so the honest budget is the
    # spend level at which the wall was actually hit. Each banner sighting records
    # the window's spend; the recent MAX is the effective ceiling.

    def record_limit_hit(self, kind: str, window_cost: float,
                         dedupe_minutes: int = 30) -> bool:
        """Record one observed limit hit. Banners persist across ticks, so hits of
        the same kind within `dedupe_minutes` are one sighting. Returns True if
        recorded."""
        now = datetime.now().astimezone()
        with self._lock:
            r = self._conn.execute(
                "SELECT MAX(observed_at) AS m FROM limit_hit WHERE kind=?", (kind,)
            ).fetchone()
        if r and r["m"]:
            try:
                last = datetime.fromisoformat(r["m"])
                if (now - last).total_seconds() < dedupe_minutes * 60:
                    return False
            except ValueError:
                pass

        def work():
            self._conn.execute(
                "INSERT OR IGNORE INTO limit_hit(observed_at, kind, window_cost) "
                "VALUES(?,?,?)",
                (now.isoformat(), kind, float(window_cost)),
            )
        self._write(work)
        return True

    def observed_ceiling(self, kind: str = "5h", days: int = 30) -> Optional[float]:
        """Best estimate of the window's real cap: the highest spend that ever hit
        the wall recently (the cap is at least that high)."""
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
        with self._lock:
            r = self._conn.execute(
                "SELECT MAX(window_cost) AS m FROM limit_hit "
                "WHERE kind=? AND observed_at >= ?",
                (kind, cutoff),
            ).fetchone()
        return float(r["m"]) if r and r["m"] is not None else None

    def day_count(self) -> int:
        with self._lock:
            r = self._conn.execute("SELECT COUNT(DISTINCT day) AS n FROM usage_daily").fetchone()
        return r["n"] if r else 0
