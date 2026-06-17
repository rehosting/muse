"""Tests for the pure outcome-compute module (no service, no transcripts)."""

from datetime import datetime, timezone
from types import SimpleNamespace

from muse import insights_outcomes as io


def _summary(sid, cost_key=None, model="sonnet", cwd="/p/a", health=None,
             day="2026-06-14"):
    return SimpleNamespace(
        session_id=sid, title=sid, project_cwd=cwd, provider="claude",
        model=model, git_branch="main", health=health,
        mtime=datetime.fromisoformat(f"{day}T12:00:00+00:00"),
    )


def _scan(events=()):
    return SimpleNamespace(events=list(events))


NOW = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)


def _compute(**over):
    base = dict(
        days=30, now=NOW, summaries=[], rollup={}, windows={}, health_rows={},
        commits_by_sid={}, history_rows=[], error_times=[], commit_times=[],
        scan=_scan(),
    )
    base.update(over)
    return io.compute_outcomes(**base)


def test_confidence_split_high_medium_ship_low_separate():
    s = _summary("s1")
    commits = {"s1": [
        {"confidence": "high", "subject": "fix", "committer_date": "2026-06-14T12:00:00+00:00"},
        {"confidence": "medium", "subject": "tweak", "committer_date": "2026-06-14T13:00:00+00:00"},
        {"confidence": "low", "subject": "maybe", "committer_date": "2026-06-14T14:00:00+00:00"},
    ]}
    r = _compute(summaries=[s], rollup={"s1": (1000, 4.0)}, commits_by_sid=commits)
    o = r.outcomes[0]
    assert o.commits_high == 1 and o.commits_medium == 1 and o.commits_low == 1
    assert o.commit_subjects == ["tweak", "fix"]  # newest first, high+medium only
    # productive ranking counts only high+medium
    assert r.most_productive and r.most_productive[0].session_id == "s1"
    # ratio excludes low
    proj = {x.key: x for x in r.by_project}["/p/a"]
    assert proj.commits == 2 and proj.commits_low == 1


def test_cost_floor_protects_ranking():
    cheap1 = _summary("cheap", cwd="/p/a")
    rich = _summary("rich", cwd="/p/b")
    r = _compute(
        summaries=[cheap1, rich],
        rollup={"cheap": (10, 0.02), "rich": (1000, 5.0)},
        commits_by_sid={
            "cheap": [{"confidence": "high", "subject": "x", "committer_date": "2026-06-14T12:00:00+00:00"}],
            "rich": [{"confidence": "high", "subject": "y", "committer_date": "2026-06-14T12:00:00+00:00"}] * 5,
        },
    )
    # With a $0.50 floor, 5 commits/$5 (=1.0) beats 1 commit/$0.50 (=2.0)? No —
    # cheap is 1/0.5=2.0, rich is 5/5=1.0, cheap wins. The floor stops a $0.001
    # session from being infinitely productive.
    assert r.most_productive[0].session_id == "cheap"


def test_wasteful_excludes_shippers_and_needs_signal():
    burned = _summary("burned", health="bad")
    shipped = _summary("shipped", health="bad")
    fine = _summary("fine", health="ok")
    r = _compute(
        summaries=[burned, shipped, fine],
        rollup={"burned": (5000, 8.0), "shipped": (5000, 8.0), "fine": (10, 0.01)},
        commits_by_sid={"shipped": [
            {"confidence": "high", "subject": "z", "committer_date": "2026-06-14T12:00:00+00:00"}
        ]},
    )
    ids = {o.session_id for o in r.most_wasteful}
    assert "burned" in ids       # bad health + no commits
    assert "shipped" not in ids  # shipped → never wasteful
    assert "fine" not in ids     # ok health, below median cost → not flagged


def test_ratio_null_under_one_dollar():
    s = _summary("s1")
    r = _compute(
        summaries=[s], rollup={"s1": (10, 0.4)},
        commits_by_sid={"s1": [
            {"confidence": "high", "subject": "x", "committer_date": "2026-06-14T12:00:00+00:00"}
        ]},
    )
    assert r.by_project[0].commits_per_10usd is None  # cost < $1


def test_duration_falls_back_to_mtime_point():
    s = _summary("s1")
    r = _compute(summaries=[s], rollup={"s1": (10, 1.0)}, windows={})
    o = r.outcomes[0]
    assert o.started == o.ended == s.mtime
    assert o.duration_seconds == 0.0


def test_duration_from_file_window():
    s = _summary("s1", day="2026-06-14")
    windows = {"s1": ("2026-06-14T10:00:00+00:00", "2026-06-14T11:30:00+00:00")}
    r = _compute(summaries=[s], rollup={"s1": (10, 1.0)}, windows=windows)
    o = r.outcomes[0]
    # started = first file op (10:00); ended = max(last op 11:30, mtime 12:00) = 12:00.
    assert o.ended == s.mtime
    assert o.duration_seconds == 2 * 3600


def test_matrix_buckets_local_time():
    # A commit at 02:00 UTC → check it lands in SOME cell with commits==1.
    r = _compute(commit_times=["2026-06-14T02:00:00+00:00"])
    total = sum(c.commits for c in r.matrix)
    assert total == 1
    assert len(r.matrix) == 7 * 24


def test_calendar_sums_across_rows():
    rows = [
        {"day": "2026-06-14", "cost_usd": 1.0, "input": 100, "output": 50, "cc": 10, "cr": 0},
        {"day": "2026-06-14", "cost_usd": 2.0, "input": 200, "output": 50, "cc": 0, "cr": 0},
        {"day": "2026-06-13", "cost_usd": 0.5, "input": 10, "output": 5, "cc": 0, "cr": 0},
    ]
    r = _compute(history_rows=rows)
    by_day = {h.day: h for h in r.calendar}
    assert round(by_day["2026-06-14"].cost_usd, 2) == 3.0
    assert by_day["2026-06-14"].work_tokens == 100 + 50 + 10 + 200 + 50
    assert r.calendar == sorted(r.calendar, key=lambda h: h.day)  # chronological
