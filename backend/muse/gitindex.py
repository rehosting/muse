"""Code provenance: link sessions to the git commits they (likely) produced.

muse-owned index (same muse.db) harvested from `git log` run READ-ONLY in the
user's project dirs — `_git` is the single chokepoint and only allowlisted
plumbing-safe subcommands pass (never anything that touches the index or
worktree). Matching is evidence-based, never authorship proof: a commit links
to a session when it lands inside the session's activity window and (ideally)
touches the same files the session edited. Every row keeps its `basis` so the
UI/MCP can show confidence honestly.

Sync follows the FileIndex pattern: cursor rows + per-repo rate limit +
per-tick cap, piggybacked on the alerts tick.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import db

_MIN_HARVEST_SECONDS = 300.0  # per-repo
_FAILED_REPROBE_SECONDS = 3600.0  # not-a-repo / erroring cwds re-probe slowly
_MAX_REPOS_PER_SYNC = 5  # cold start over ~40 cwds must not stall one tick
_MIN_REMATCH_SECONDS = 120.0
_FORCED_REMATCH_FLOOR_SECONDS = 60.0
_FIRST_HARVEST_MAX = 2000  # provenance only needs history overlapping sessions
_MAX_FILES_PER_COMMIT = 200  # vendored mega-commits; file_count keeps the truth
_CURSOR_OVERLAP_SECONDS = 600  # idempotent overlap (INSERT OR IGNORE dedupes)

# Matching windows + weights (see score_commit).
_SLACK_BEFORE = timedelta(minutes=5)
_SLACK_AFTER = timedelta(minutes=30)
_SCORE_MIN = 3.0

_ALLOWED_SUBCOMMANDS = {"log", "rev-parse"}

_REC = "\x1e"  # record separator in --pretty
_FIELD = "\x1f"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS git_repos (
    toplevel        TEXT PRIMARY KEY,
    cwds            TEXT,              -- JSON list of project_cwds mapping here
    last_harvest_ts TEXT,              -- committer-date cursor (ISO)
    harvested_at    REAL,              -- wall clock of last attempt (rate limit)
    ok              INTEGER NOT NULL DEFAULT 1,
    error           TEXT
);
CREATE TABLE IF NOT EXISTS git_commits (
    repo           TEXT NOT NULL,
    commit_hash    TEXT NOT NULL,
    author         TEXT,
    author_email   TEXT,
    author_date    TEXT,
    committer_date TEXT,
    subject        TEXT,
    ref_hint       TEXT,
    file_count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, commit_hash)
);
CREATE INDEX IF NOT EXISTS gc_repo_date ON git_commits(repo, committer_date);
CREATE TABLE IF NOT EXISTS git_commit_files (
    repo        TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    PRIMARY KEY (repo, commit_hash, rel_path)
);
CREATE INDEX IF NOT EXISTS gcf_path ON git_commit_files(rel_path);
CREATE TABLE IF NOT EXISTS commit_session (
    repo        TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    score       REAL NOT NULL,
    confidence  TEXT NOT NULL,         -- high | medium | low
    basis       TEXT NOT NULL,         -- JSON: which signals fired
    matched_at  REAL,
    PRIMARY KEY (repo, commit_hash, session_id)
);
CREATE INDEX IF NOT EXISTS cs_session ON commit_session(session_id);
CREATE INDEX IF NOT EXISTS cs_hash ON commit_session(commit_hash);
"""


def _git(directory: str, *args: str, timeout: float = 8.0) -> Optional[str]:
    """Run an allowlisted read-only git command; returns stdout or None. Never
    raises — slow disks / vanished repos must never take down a tick."""
    if not args or args[0] not in _ALLOWED_SUBCOMMANDS:
        return None
    try:
        p = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def _short_ref(ref_hint: Optional[str]) -> Optional[str]:
    if not ref_hint:
        return None
    for prefix in ("refs/heads/", "refs/remotes/origin/", "refs/remotes/"):
        if ref_hint.startswith(prefix):
            return ref_hint[len(prefix):]
    return ref_hint


def score_commit(
    commit_dt: Optional[datetime],
    commit_files: set[str],
    ref_hint: Optional[str],
    window: tuple[Optional[datetime], Optional[datetime]],
    session_rel_files: set[str],
    session_branch: Optional[str],
) -> Optional[tuple[float, dict]]:
    """Pure scoring of one (commit, session) pair. Returns (score, basis) or
    None when the time gate fails. The time gate is REQUIRED — without temporal
    overlap there is no provenance claim at all."""
    first, last = window
    if commit_dt is None or first is None or last is None:
        return None
    if not (first - _SLACK_BEFORE <= commit_dt <= last + _SLACK_AFTER):
        return None
    in_window = first <= commit_dt <= last
    score = 2.0 if in_window else 1.0
    basis: dict = {"in_window": in_window, "slack": not in_window}

    shared = sorted(commit_files & session_rel_files)
    if commit_files and shared:
        coverage = len(shared) / len(commit_files)
        score += 5.0 * coverage
        basis["shared_files"] = shared[:20]
        basis["coverage"] = round(coverage, 3)

    branch = _short_ref(ref_hint)
    if session_branch and branch and session_branch == branch:
        score += 1.0
        basis["branch_match"] = branch

    return score, basis


def _confidence(score: float) -> str:
    if score >= 6:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class GitIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = db.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._toplevel_cache: dict[str, Optional[str]] = {}  # cwd -> toplevel|None
        self._last_rematch = 0.0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- repo resolution -------------------------------------------------------
    def _resolve(self, cwd: str) -> Optional[str]:
        if cwd in self._toplevel_cache:
            return self._toplevel_cache[cwd]
        out = _git(cwd, "rev-parse", "--show-toplevel", timeout=4.0)
        top = out.strip() if out else None
        self._toplevel_cache[cwd] = top
        return top

    def toplevel_for(self, cwd: str) -> Optional[str]:
        """cwd -> repo toplevel (cached; None when not a repo)."""
        return self._resolve(cwd)

    # --- harvest ------------------------------------------------------------------
    def sync(self, cwds: set[str]) -> int:
        """Resolve cwds to repos, harvest new commits (rate-limited, capped per
        call). Returns the number of newly inserted commits."""
        now = time.time()
        with self._lock:
            repo_rows = {
                r["toplevel"]: dict(r)
                for r in self._conn.execute("SELECT * FROM git_repos").fetchall()
            }

        # Map cwds onto repos (merging into cwds JSON); record failures.
        repo_cwds: dict[str, set[str]] = {
            top: set(json.loads(r["cwds"] or "[]")) for top, r in repo_rows.items()
        }
        for cwd in sorted(cwds):
            if not cwd:
                continue
            prev_fail = repo_rows.get(cwd)
            if (
                prev_fail is not None
                and not prev_fail["ok"]
                and (now - (prev_fail["harvested_at"] or 0)) < _FAILED_REPROBE_SECONDS
            ):
                continue  # known non-repo; re-probe slowly
            top = self._resolve(cwd)
            if top is None:
                self._upsert_repo(cwd, cwds=[cwd], ok=0, error="not a git repo",
                                  harvested_at=now)
                continue
            if prev_fail is not None and not prev_fail["ok"] and top:
                # A previously-failed cwd became a repo: drop the failure row.
                with self._lock:
                    self._conn.execute("DELETE FROM git_repos WHERE toplevel=?", (cwd,))
                    self._conn.commit()
                repo_rows.pop(cwd, None)
            repo_cwds.setdefault(top, set()).add(cwd)

        # Pick repos due for harvest: oldest first, capped.
        due: list[str] = []
        for top in repo_cwds:
            row = repo_rows.get(top)
            last = row["harvested_at"] if row else None
            if row is not None and not row["ok"]:
                if (now - (last or 0)) < _FAILED_REPROBE_SECONDS:
                    continue
            elif last is not None and (now - last) < _MIN_HARVEST_SECONDS:
                continue
            due.append(top)
        due.sort(key=lambda t: (repo_rows.get(t) or {}).get("harvested_at") or 0)

        inserted = 0
        for top in due[:_MAX_REPOS_PER_SYNC]:
            row = repo_rows.get(top)
            cursor = row["last_harvest_ts"] if row else None
            new, err, max_ts = self._harvest(top, cursor)
            inserted += new
            self._upsert_repo(
                top,
                cwds=sorted(repo_cwds[top]),
                ok=0 if err else 1,
                error=err,
                harvested_at=now,
                last_harvest_ts=max_ts or cursor,
            )

        # Prune repos whose every mapped cwd vanished from the session corpus.
        with self._lock:
            for top, row in repo_rows.items():
                mapped = set(json.loads(row["cwds"] or "[]"))
                if mapped and not (mapped & cwds):
                    for table in ("git_commits", "git_commit_files", "commit_session"):
                        self._conn.execute(
                            f"DELETE FROM {table} WHERE repo=?", (top,)  # noqa: S608
                        )
                    self._conn.execute("DELETE FROM git_repos WHERE toplevel=?", (top,))
            self._conn.commit()
        return inserted

    def _upsert_repo(self, toplevel: str, cwds: list[str], ok: int,
                     error: Optional[str], harvested_at: float,
                     last_harvest_ts: Optional[str] = None) -> None:
        def work():
            with self._lock:
                try:
                    self._conn.execute(
                        "INSERT INTO git_repos(toplevel, cwds, last_harvest_ts, "
                        "harvested_at, ok, error) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(toplevel) DO UPDATE SET cwds=excluded.cwds, "
                        "last_harvest_ts=COALESCE(excluded.last_harvest_ts, "
                        "git_repos.last_harvest_ts), harvested_at=excluded.harvested_at, "
                        "ok=excluded.ok, error=excluded.error",
                        (toplevel, json.dumps(cwds), last_harvest_ts, harvested_at,
                         ok, error),
                    )
                    self._conn.commit()
                except sqlite3.OperationalError:
                    self._conn.rollback()
                    raise

        db.retry_locked(work)

    def _harvest(
        self, toplevel: str, cursor: Optional[str]
    ) -> tuple[int, Optional[str], Optional[str]]:
        """One `git log` harvest. Returns (new_commits, error, max_committer_date).
        Timestamp cursor (not hash..HEAD): survives rebase/amend/force-push;
        the overlap window is idempotent via INSERT OR IGNORE."""
        args = [
            "log", "--all", "--source", "--name-only", "--date=iso-strict",
            f"--pretty=format:{_REC}%H{_FIELD}%S{_FIELD}%an{_FIELD}%ae{_FIELD}"
            f"%aI{_FIELD}%cI{_FIELD}%s",
        ]
        if cursor:
            since_dt = _parse_dt(cursor)
            if since_dt is not None:
                since = (since_dt - timedelta(seconds=_CURSOR_OVERLAP_SECONDS)).isoformat()
                args.append(f"--since={since}")
            else:
                args.append(f"--max-count={_FIRST_HARVEST_MAX}")
        else:
            args.append(f"--max-count={_FIRST_HARVEST_MAX}")
        out = _git(toplevel, *args)
        if out is None:
            return 0, "git log failed (timeout, or repo gone)", None

        inserted = 0
        max_ts: Optional[str] = cursor
        records = [r for r in out.split(_REC) if r.strip()]

        def work():
            nonlocal inserted, max_ts
            with self._lock:
                try:
                    for rec in records:
                        lines = rec.strip("\n").split("\n")
                        fields = lines[0].split(_FIELD)
                        if len(fields) != 7:
                            continue
                        h, ref, an, ae, a_date, c_date, subject = fields
                        files = [ln.strip() for ln in lines[1:] if ln.strip()]
                        cur = self._conn.execute(
                            "INSERT OR IGNORE INTO git_commits(repo, commit_hash, "
                            "author, author_email, author_date, committer_date, "
                            "subject, ref_hint, file_count) VALUES(?,?,?,?,?,?,?,?,?)",
                            (toplevel, h, an, ae, a_date, c_date, subject,
                             ref or None, len(files)),
                        )
                        if cur.rowcount > 0:
                            inserted += 1
                            for rel in files[:_MAX_FILES_PER_COMMIT]:
                                self._conn.execute(
                                    "INSERT OR IGNORE INTO git_commit_files(repo, "
                                    "commit_hash, rel_path) VALUES(?,?,?)",
                                    (toplevel, h, rel),
                                )
                        if c_date and (max_ts is None or c_date > max_ts):
                            max_ts = c_date
                    self._conn.commit()
                except sqlite3.OperationalError:
                    self._conn.rollback()
                    raise

        try:
            db.retry_locked(work)
        except sqlite3.OperationalError:
            return 0, "database is locked", None
        return inserted, None, max_ts

    # --- matching -------------------------------------------------------------------
    def rematch(self, sessions: list[dict], force: bool = False) -> int:
        """Score every session against its repo's commits in-window. Each entry:
        {session_id, cwd, branch, first_ts, last_ts, files: set[str] (absolute)}.
        Delete+reinsert per session (the FileIndex idiom). Rate-limited globally —
        the per-session work is one indexed range query + set intersections."""
        now = time.time()
        # `force` (new commits landed) shortens the wait but never goes below
        # the floor: during a cold multi-repo harvest every tick inserts, and
        # rematching ~100 sessions per 5s tick pegged a core (observed live).
        min_wait = _FORCED_REMATCH_FLOOR_SECONDS if force else _MIN_REMATCH_SECONDS
        if (now - self._last_rematch) < min_wait:
            return 0
        self._last_rematch = now

        matched = 0
        for sess in sessions:
            cwd = sess.get("cwd")
            first, last = sess.get("first_ts"), sess.get("last_ts")
            if not cwd or first is None or last is None:
                continue
            top = self._toplevel_cache.get(cwd)
            if top is None:
                continue  # only repos sync() already resolved; no probing here
            rel_files = {
                f[len(top) + 1:] for f in sess.get("files", set())
                if f.startswith(top + "/")
            }
            lo = (first - _SLACK_BEFORE).isoformat()
            hi = (last + _SLACK_AFTER).isoformat()
            with self._lock:
                commits = self._conn.execute(
                    "SELECT commit_hash, committer_date, ref_hint FROM git_commits "
                    "WHERE repo=? AND committer_date >= ? AND committer_date <= ?",
                    (top, lo, hi),
                ).fetchall()
                # ONE files query for the whole window (a per-commit query here
                # multiplied into tens of thousands of queries per rematch on
                # long-window sessions).
                files_by_hash: dict[str, set[str]] = {}
                for r in self._conn.execute(
                    "SELECT gcf.commit_hash, gcf.rel_path FROM git_commit_files gcf "
                    "JOIN git_commits gc ON gc.repo = gcf.repo "
                    "AND gc.commit_hash = gcf.commit_hash "
                    "WHERE gcf.repo=? AND gc.committer_date >= ? AND gc.committer_date <= ?",
                    (top, lo, hi),
                ).fetchall():
                    files_by_hash.setdefault(r["commit_hash"], set()).add(r["rel_path"])
            rows = []
            for c in commits:
                cfiles = files_by_hash.get(c["commit_hash"], set())
                scored = score_commit(
                    _parse_dt(c["committer_date"]), cfiles, c["ref_hint"],
                    (first, last), rel_files, sess.get("branch"),
                )
                if scored is None or scored[0] < _SCORE_MIN:
                    continue
                rows.append((top, c["commit_hash"], sess["session_id"], scored[0],
                             _confidence(scored[0]), json.dumps(scored[1]), now))

            def work(sid=sess["session_id"], rows=rows):
                with self._lock:
                    try:
                        self._conn.execute(
                            "DELETE FROM commit_session WHERE session_id=?", (sid,)
                        )
                        self._conn.executemany(
                            "INSERT OR REPLACE INTO commit_session(repo, commit_hash, "
                            "session_id, score, confidence, basis, matched_at) "
                            "VALUES(?,?,?,?,?,?,?)",
                            rows,
                        )
                        self._conn.commit()
                    except sqlite3.OperationalError:
                        self._conn.rollback()
                        raise

            try:
                db.retry_locked(work)
                matched += len(rows)
            except sqlite3.OperationalError:
                continue
        return matched

    # --- queries -----------------------------------------------------------------------
    def commits_for_session(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT cs.repo, cs.commit_hash, cs.score, cs.confidence, cs.basis, "
                "gc.author, gc.committer_date, gc.subject, gc.ref_hint, gc.file_count "
                "FROM commit_session cs JOIN git_commits gc "
                "ON gc.repo = cs.repo AND gc.commit_hash = cs.commit_hash "
                "WHERE cs.session_id=? ORDER BY gc.committer_date DESC",
                (session_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["basis"] = json.loads(d["basis"] or "{}")
            d["files"] = self._files_for(r["repo"], r["commit_hash"])
            out.append(d)
        return out

    def sessions_for_commit(self, hash_prefix: str) -> list[dict]:
        like = hash_prefix.lower() + "%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT cs.session_id, cs.score, cs.confidence, cs.basis, cs.repo, "
                "cs.commit_hash, gc.subject, gc.committer_date, gc.author "
                "FROM commit_session cs JOIN git_commits gc "
                "ON gc.repo = cs.repo AND gc.commit_hash = cs.commit_hash "
                "WHERE cs.commit_hash LIKE ? ORDER BY cs.score DESC",
                (like,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["basis"] = json.loads(d["basis"] or "{}")
            out.append(d)
        return out

    def _files_for(self, repo: str, commit_hash: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT rel_path FROM git_commit_files WHERE repo=? AND commit_hash=? "
                "ORDER BY rel_path",
                (repo, commit_hash),
            ).fetchall()
        return [r["rel_path"] for r in rows]

    def commit_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM git_commits").fetchone()
        return row["n"] if row else 0
