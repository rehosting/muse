"""Tests for the AI job store + worker lifecycle (no claude binary involved)."""

import pytest

from muse.ai.jobs import AIJobStore, AIWorker


@pytest.fixture
def store(tmp_path):
    s = AIJobStore(tmp_path / "muse.db")
    yield s
    s.close()


def test_enqueue_and_get(store):
    j = store.enqueue("ask", {"question": "what broke?"}, model="sonnet")
    assert j.id.startswith("aij_") and j.status == "queued"
    got = store.get(j.id)
    assert got is not None
    assert got.params == {"question": "what broke?"} and got.model == "sonnet"


def test_unknown_kind_rejected(store):
    with pytest.raises(ValueError):
        store.enqueue("make_coffee", {})


def test_claim_is_fifo_and_moves_to_running(store):
    a = store.enqueue("ask", {"q": 1})
    b = store.enqueue("ask", {"q": 2})
    first = store.claim_next()
    assert first is not None and first.id == a.id and first.status == "running"
    second = store.claim_next()
    assert second is not None and second.id == b.id
    assert store.claim_next() is None


def test_finish_done_and_error(store):
    j = store.enqueue("ask", {})
    store.claim_next()
    store.finish(j.id, result={"answer_md": "hi"}, cost_usd=0.01, duration_ms=1200)
    got = store.get(j.id)
    assert got.status == "done" and got.result == {"answer_md": "hi"}
    assert got.cost_usd == 0.01 and got.finished_at

    k = store.enqueue("ask", {})
    store.claim_next()
    store.finish(k.id, error="boom")
    assert store.get(k.id).status == "error"
    assert store.last_error() == "boom"


def test_cancel_only_queued(store):
    j = store.enqueue("ask", {})
    assert store.cancel(j.id) is True
    assert store.get(j.id).status == "cancelled"
    assert store.claim_next() is None  # cancelled jobs are never claimed

    k = store.enqueue("ask", {})
    store.claim_next()
    assert store.cancel(k.id) is False  # running: handled by the worker instead


def test_orphan_recovery_on_reopen(tmp_path):
    s = AIJobStore(tmp_path / "muse.db")
    j = s.enqueue("ask", {})
    s.claim_next()
    s.close()
    # Simulates a server crash with a job mid-flight.
    s2 = AIJobStore(tmp_path / "muse.db")
    got = s2.get(j.id)
    assert got.status == "error" and "interrupted" in got.error
    s2.close()


def test_has_pending_dedupe(store):
    store.enqueue("daily_digest", {"day": "2026-06-10"})
    assert store.has_pending("daily_digest")
    assert store.has_pending("daily_digest", {"day": "2026-06-10"})
    assert not store.has_pending("daily_digest", {"day": "2026-06-09"})
    assert not store.has_pending("weekly_retro")


def test_counts_and_total_cost(store):
    store.enqueue("ask", {})
    j = store.enqueue("ask", {})
    store.claim_next()
    store.finish(j.id, result={}, cost_usd=0.25)
    # claim_next claimed the FIRST job; finish targeted the second explicitly,
    # so statuses are one running + one done.
    c = store.counts()
    assert c.get("running") == 1 and c.get("done") == 1
    assert store.total_cost() == 0.25


def test_worker_run_one_success_and_error(store):
    j = store.enqueue("ask", {"question": "x"})
    claimed = store.claim_next()

    def execute_ok(job):
        return {"answer_md": "hello", "_meta": {"cost_usd": 0.1, "duration_ms": 5,
                                                "model": "sonnet"}}

    w = AIWorker(store=store, execute=execute_ok)
    w.run_one(claimed)
    got = store.get(j.id)
    assert got.status == "done"
    assert got.result == {"answer_md": "hello"}  # _meta stripped from result
    assert got.cost_usd == 0.1 and got.model == "sonnet"

    k = store.enqueue("ask", {})
    claimed = store.claim_next()
    w_err = AIWorker(store=store, execute=lambda job: (_ for _ in ()).throw(RuntimeError("nope")))
    w_err.run_one(claimed)
    assert store.get(k.id).status == "error"
    assert "nope" in store.get(k.id).error
