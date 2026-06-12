"""Tests for the git provenance index: harvest from a real fixture repo +
pure-function matching."""

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from muse.gitindex import GitIndex, _MAX_REPOS_PER_SYNC, score_commit

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _git(repo, *args, env=None):
    e = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T",
         "GIT_COMMITTER_EMAIL": "t@x", "HOME": str(repo), "PATH": "/usr/bin:/bin"}
    if env:
        e.update(env)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=e, check=True)


def _commit(repo, fname, msg, when):
    (repo / fname).parent.mkdir(parents=True, exist_ok=True)
    (repo / fname).write_text(f"{msg}\n")
    _git(repo, "add", ".")
    iso = when.isoformat()
    _git(repo, "commit", "-m", msg,
         env={"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso})
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    return r


@pytest.fixture
def index(tmp_path):
    gi = GitIndex(tmp_path / "muse.db")
    yield gi
    gi.close()


def test_initial_harvest(index, repo):
    h1 = _commit(repo, "a.py", "add a", T0)
    h2 = _commit(repo, "src/b.py", "add b", T0 + timedelta(minutes=10))
    n = index.sync({str(repo)})
    assert n == 2
    assert index.commit_count() == 2
    # Files + ref hint landed.
    rows = index.sessions_for_commit(h1[:8])
    assert rows == []  # no matches yet, but the commit exists
    assert index._files_for(str(repo), h2) == ["src/b.py"]


def test_incremental_harvest_no_duplicates(index, repo, monkeypatch):
    _commit(repo, "a.py", "one", T0)
    assert index.sync({str(repo)}) == 1
    # Within the rate limit window: nothing happens.
    assert index.sync({str(repo)}) == 0
    # Force past the rate limit; a new commit arrives.
    monkeypatch.setattr("muse.gitindex._MIN_HARVEST_SECONDS", 0.0)
    _commit(repo, "b.py", "two", T0 + timedelta(hours=1))
    assert index.sync({str(repo)}) == 1
    assert index.commit_count() == 2
    # Overlap window doesn't duplicate.
    assert index.sync({str(repo)}) == 0


def test_amend_survives_timestamp_cursor(index, repo, monkeypatch):
    monkeypatch.setattr("muse.gitindex._MIN_HARVEST_SECONDS", 0.0)
    _commit(repo, "a.py", "original", T0)
    index.sync({str(repo)})
    iso = (T0 + timedelta(minutes=5)).isoformat()
    _git(repo, "commit", "--amend", "-m", "amended",
         env={"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso})
    n = index.sync({str(repo)})
    assert n == 1  # the amended commit (new hash) is harvested; no crash


def test_not_a_repo_marked_and_backed_off(index, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert index.sync({str(plain)}) == 0
    with index._lock:
        row = index._conn.execute(
            "SELECT * FROM git_repos WHERE toplevel=?", (str(plain),)
        ).fetchone()
    assert row is not None and row["ok"] == 0


def test_subdir_dedupes_to_toplevel(index, repo):
    _commit(repo, "src/c.py", "c", T0)
    sub = repo / "src"
    index.sync({str(repo), str(sub)})
    with index._lock:
        rows = index._conn.execute("SELECT toplevel, cwds FROM git_repos").fetchall()
    assert len(rows) == 1
    assert str(sub) in rows[0]["cwds"]


def test_per_sync_repo_cap(index, tmp_path):
    repos = []
    for i in range(_MAX_REPOS_PER_SYNC + 2):
        r = tmp_path / f"r{i}"
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        _commit(r, "f.py", "x", T0)
        repos.append(str(r))
    index.sync(set(repos))
    with index._lock:
        harvested = index._conn.execute(
            "SELECT COUNT(*) AS n FROM git_repos WHERE last_harvest_ts IS NOT NULL"
        ).fetchone()["n"]
    assert harvested == _MAX_REPOS_PER_SYNC


def test_prune_vanished_repo(index, repo, monkeypatch):
    _commit(repo, "a.py", "x", T0)
    index.sync({str(repo)})
    assert index.commit_count() == 1
    index.sync(set())  # the cwd vanished from the corpus
    assert index.commit_count() == 0


# --- score_commit (pure) ---------------------------------------------------------

WIN = (T0, T0 + timedelta(hours=2))


def test_score_time_gate_required():
    assert score_commit(None, set(), None, WIN, set(), None) is None
    way_after = T0 + timedelta(hours=3)
    assert score_commit(way_after, set(), None, WIN, set(), None) is None
    before = T0 - timedelta(minutes=30)
    assert score_commit(before, set(), None, WIN, set(), None) is None


def test_score_in_window_vs_slack():
    inside, _ = score_commit(T0 + timedelta(hours=1), set(), None, WIN, set(), None)
    slack, basis = score_commit(WIN[1] + timedelta(minutes=10), set(), None, WIN, set(), None)
    assert inside == 2.0 and slack == 1.0
    assert basis["slack"] is True


def test_score_file_coverage():
    cfiles = {"src/a.py", "src/b.py"}
    full, basis = score_commit(T0, cfiles, None, WIN, {"src/a.py", "src/b.py"}, None)
    assert full == 7.0 and basis["coverage"] == 1.0
    half, _ = score_commit(T0, cfiles, None, WIN, {"src/a.py"}, None)
    assert half == 4.5


def test_score_branch_bonus_weak():
    s, basis = score_commit(T0, set(), "refs/heads/main", WIN, set(), "main")
    assert s == 3.0 and basis["branch_match"] == "main"
    s2, basis2 = score_commit(T0, set(), "refs/heads/dev", WIN, set(), "main")
    assert s2 == 2.0 and "branch_match" not in basis2


# --- rematch end-to-end ------------------------------------------------------------


def test_rematch_links_commit_to_session(index, repo):
    h = _commit(repo, "src/core.py", "fix the bug", T0 + timedelta(minutes=30))
    index.sync({str(repo)})
    sessions = [{
        "session_id": "sess1",
        "cwd": str(repo),
        "branch": "main",
        "first_ts": T0,
        "last_ts": T0 + timedelta(hours=1),
        "files": {f"{repo}/src/core.py"},  # absolute, relativized internally
    }]
    n = index.rematch(sessions, force=True)
    assert n == 1
    rows = index.commits_for_session("sess1")
    assert len(rows) == 1
    r = rows[0]
    assert r["commit_hash"] == h
    assert r["confidence"] == "high"  # in window (+2) + full coverage (+5) + branch (+1)
    assert r["basis"]["shared_files"] == ["src/core.py"]
    # Reverse lookup by prefix.
    hits = index.sessions_for_commit(h[:8])
    assert hits and hits[0]["session_id"] == "sess1"


def test_rematch_time_only_is_low_confidence(index, repo):
    _commit(repo, "other.py", "unrelated files", T0 + timedelta(minutes=30))
    index.sync({str(repo)})
    sessions = [{
        "session_id": "sess2", "cwd": str(repo), "branch": "main",
        "first_ts": T0, "last_ts": T0 + timedelta(hours=1),
        "files": set(),
    }]
    index.rematch(sessions, force=True)
    rows = index.commits_for_session("sess2")
    assert len(rows) == 1 and rows[0]["confidence"] == "low"  # +2 time +1 branch


def test_rematch_outside_window_no_link(index, repo):
    _commit(repo, "a.py", "way later", T0 + timedelta(days=2))
    index.sync({str(repo)})
    sessions = [{
        "session_id": "sess3", "cwd": str(repo), "branch": None,
        "first_ts": T0, "last_ts": T0 + timedelta(hours=1), "files": set(),
    }]
    index.rematch(sessions, force=True)
    assert index.commits_for_session("sess3") == []
