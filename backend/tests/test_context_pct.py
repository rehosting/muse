"""Context-window occupancy %. The window is inferred PER MODEL from the largest
prompt seen for that model, so two sessions on the same model share one window —
a low-usage session must not read ~5x too high against an assumed 200k."""

from datetime import datetime, timezone

from muse.usage_cache import Event, Scan, _window_for_peak, context_pcts


def _ev(sid, ctx, model, secs):
    # context = input + cc + cr; put it all in cache_read for simplicity.
    return Event(
        sid=sid,
        project_dir="p",
        ts=datetime(2026, 7, 4, 0, 0, secs, tzinfo=timezone.utc),
        input=0,
        output=10,
        cc=0,
        cr=ctx,
        model=model,
        is_subagent=False,
        agent_type="",
    )


def test_window_for_peak_buckets_at_200k():
    assert _window_for_peak(150_000) == 200_000
    assert _window_for_peak(200_000) == 200_000
    assert _window_for_peak(200_001) == 1_000_000


def test_same_model_shares_a_window():
    # One opus session crossed 200k (proving a 1M window); another opus session
    # is only at 100k. Both must be measured against 1M — the small one ~10%,
    # NOT 50% against a wrongly-assumed 200k.
    scan = Scan(
        events=[
            _ev("big", 300_000, "opus", 1),
            _ev("small", 100_000, "opus", 2),
        ],
        sessions=2,
        sessions_by_project={},
    )
    pct = context_pcts(scan)
    assert round(pct["big"]) == 30
    assert round(pct["small"]) == 10


def test_distinct_models_get_distinct_windows():
    # A model that never exceeded 200k keeps the 200k window.
    scan = Scan(
        events=[
            _ev("a", 300_000, "opus", 1),  # opus → 1M
            _ev("b", 100_000, "haiku", 2),  # haiku stayed small → 200k
        ],
        sessions=2,
        sessions_by_project={},
    )
    pct = context_pcts(scan)
    assert round(pct["a"]) == 30  # 300k / 1M
    assert round(pct["b"]) == 50  # 100k / 200k


def test_latest_event_wins_per_session():
    scan = Scan(
        events=[
            _ev("s", 50_000, "haiku", 1),
            _ev("s", 120_000, "haiku", 5),  # later → this is the current context
        ],
        sessions=1,
        sessions_by_project={},
    )
    assert round(context_pcts(scan)["s"]) == 60  # 120k / 200k
