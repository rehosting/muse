"""Deterministic failure-pattern detection + the session_health snapshot store."""

from datetime import datetime, timezone

import pytest

from muse.models import SessionEvent, SessionSummary
from muse.patterns import HealthStore, detect_patterns


def _call(i, tool, label, tid):
    return SessionEvent(index=i, kind="tool_call", type="assistant",
                        tool_name=tool, label=label, tool_use_id=tid)


def _result(i, tid, err=True):
    return SessionEvent(index=i, kind="tool_result", type="user",
                        tool_use_id=tid, is_error=err)


def test_retry_loop_detected():
    events = []
    for n in range(3):
        events.append(_call(2 * n, "Bash", "npm install --save-dev foo", f"t{n}"))
        events.append(_result(2 * n + 1, f"t{n}"))
    out = detect_patterns(events)
    assert len(out["retry_loops"]) == 1
    loop = out["retry_loops"][0]
    assert loop["tool"] == "Bash" and loop["times"] == 3
    assert loop["anchors"] == ["t0", "t1", "t2"]
    assert out["score"] == "bad"


def test_retry_loop_requires_errors_and_same_label():
    # Same calls but the results succeed → not a loop.
    ok = []
    for n in range(3):
        ok.append(_call(2 * n, "Bash", "npm install", f"t{n}"))
        ok.append(_result(2 * n + 1, f"t{n}", err=False))
    assert detect_patterns(ok)["retry_loops"] == []
    # Different labels → separate runs, not a loop.
    diff = []
    for n in range(3):
        diff.append(_call(2 * n, "Bash", f"completely different command #{n} {'x' * 50}", f"t{n}"))
        diff.append(_result(2 * n + 1, f"t{n}"))
    assert detect_patterns(diff)["retry_loops"] == []


def test_error_spiral():
    events = []
    for n in range(10):
        events.append(_result(n, f"t{n}", err=(n % 2 == 0)))  # 5/10 errors
    out = detect_patterns(events)
    assert len(out["error_spirals"]) == 1
    assert out["error_spirals"][0]["errors"] == 5
    assert out["score"] == "bad"


def test_permission_denials_cluster():
    events = [
        SessionEvent(index=i, kind="tool_result", type="user", is_error=True,
                     label="Edit", detail="Permission denied: /etc/shadow",
                     tool_use_id=f"t{i}")
        for i in range(3)
    ]
    out = detect_patterns(events)
    assert len(out["permission_denials"]) == 3
    assert out["score"] == "bad"
    # below the cluster threshold they don't surface as a pattern
    assert detect_patterns(events[:2])["permission_denials"] == []


def test_clean_session_is_ok():
    events = [
        SessionEvent(index=0, kind="user", type="user", label="hi"),
        _call(1, "Read", "main.py", "t1"),
        _result(2, "t1", err=False),
    ]
    out = detect_patterns(events)
    assert out["score"] == "ok" and out["error_count"] == 0


def _summary(sid, mtime):
    return SessionSummary(session_id=sid, provider="claude", project_dir="p",
                          title=sid, mtime=datetime.fromtimestamp(mtime, tz=timezone.utc))


@pytest.fixture
def store(tmp_path):
    s = HealthStore(tmp_path / "muse.db")
    yield s
    s.close()


def test_health_store_sync_and_scores(store):
    bad_events = []
    for n in range(3):
        bad_events.append(_call(2 * n, "Bash", "make", f"t{n}"))
        bad_events.append(_result(2 * n + 1, f"t{n}"))

    def events_fn(sid):
        return bad_events if sid == "bad" else []

    n = store.sync([_summary("bad", 1.0), _summary("good", 1.0)], events_fn)
    assert n == 2
    assert store.scores() == {"bad": "bad", "good": "ok"}
    detail = store.get("bad")
    assert detail["retry_loops"][0]["times"] == 3
    # unchanged mtime → no rescore; vanished session → pruned
    assert store.sync([_summary("bad", 1.0)], events_fn) == 0
    assert "good" not in store.scores()
