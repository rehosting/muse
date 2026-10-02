"""Queued replies: store FIFO bookkeeping, guarded controller delivery, and the
REST endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse.autopilot import controller as ctl_mod
from muse.autopilot.controller import AutopilotController
from muse.autopilot.store import AutopilotStore
from muse.models import LiveSession
from muse.routers import queue as queue_router


@pytest.fixture
def store(tmp_path):
    s = AutopilotStore(tmp_path / "test.db")
    yield s
    s.close()


# --- store ---------------------------------------------------------------------


def test_queue_fifo_and_counts(store):
    a = store.queue_add("s1", "first")
    b = store.queue_add("s1", "second")
    store.queue_add("s2", "other")
    assert store.queue_counts() == {"s1": 2, "s2": 1}
    assert store.queue_next("s1").id == a.id
    store.queue_mark(a.id, "sent")
    assert store.queue_next("s1").id == b.id
    assert store.queue_counts()["s1"] == 1


def test_queue_cancel_only_pending(store):
    item = store.queue_add("s1", "x")
    assert store.queue_cancel("s1", item.id) is True
    assert store.queue_cancel("s1", item.id) is False  # already cancelled
    sent = store.queue_add("s1", "y")
    store.queue_mark(sent.id, "sent")
    assert store.queue_cancel("s1", sent.id) is False  # already delivered
    assert store.queue_next("s1") is None


def test_queue_for_lists_pending_and_history(store):
    a = store.queue_add("s1", "done")
    store.queue_mark(a.id, "sent")
    b = store.queue_add("s1", "waiting")
    rows = store.queue_for("s1")
    assert [(r.id, r.status) for r in rows] == [(a.id, "sent"), (b.id, "pending")]


def test_queue_batch_and_mode(store):
    a = store.queue_add("s1", "first")
    b = store.queue_add("s1", "and also this", mode="append")
    c = store.queue_add("s1", "separate turn")
    # first + its trailing appends batch together; the 'turn' item waits.
    assert [i.id for i in store.queue_next_batch("s1")] == [a.id, b.id]
    # flip c to append → it joins the batch
    assert store.queue_set_mode("s1", c.id, "append") is True
    assert [i.id for i in store.queue_next_batch("s1")] == [a.id, b.id, c.id]
    # a sent item can't change mode
    store.queue_mark(a.id, "sent")
    assert store.queue_set_mode("s1", a.id, "append") is False
    # after 'a' is gone, the batch restarts at 'b' (append glues to nothing → leads)
    assert [i.id for i in store.queue_next_batch("s1")] == [b.id, c.id]


# --- controller delivery ---------------------------------------------------------


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ctl_mod, "get_settings", lambda: type("S", (), {"db_path": tmp_path / "t.db",
                                                        "ai_daily_budget_usd": 0.0})()
    )
    c = AutopilotController()
    c._sent: list[tuple] = []
    monkeypatch.setattr(
        ctl_mod.tmux, "send_text",
        lambda p, t, submit=True: (c._sent.append((p, t)) or (True, "")),
    )
    yield c
    c.store.close()


def _live(monkeypatch, status="idle", waiting_for=None, pane="%1"):
    ls = LiveSession(session_id="s1", pid=1, status=status,
                     waiting_for=waiting_for, pane_id=pane)
    monkeypatch.setattr(ctl_mod.live_discovery, "discover", lambda: [ls])


def test_deliver_when_idle(controller, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: "❯ plain prompt")
    item = controller.store.queue_add("s1", "go on")
    assert controller.deliver_queued() == [item.id]
    assert controller._sent == [("%1", "go on")]
    assert controller.store.queue_next("s1") is None
    row = controller.store.queue_for("s1")[0]
    assert row.status == "sent" and row.sent_at is not None


def test_no_delivery_while_busy_or_waiting(controller, monkeypatch):
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: "")
    controller.store.queue_add("s1", "later")
    _live(monkeypatch, status="busy")
    assert controller.deliver_queued() == []
    _live(monkeypatch, status="idle", waiting_for="permission")
    assert controller.deliver_queued() == []
    _live(monkeypatch, status="idle", pane=None)
    assert controller.deliver_queued() == []
    assert controller._sent == []


def test_no_delivery_over_visible_menu(controller, monkeypatch):
    _live(monkeypatch)
    menu = (
        "Do you want to proceed?\n"
        "❯ 1. Yes\n"
        "  2. Yes, and don't ask again\n"
        "  3. No\n"
    )
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: menu)
    controller.store.queue_add("s1", "2 looks fine")  # digits would SELECT — must not send
    assert controller.deliver_queued() == []
    assert controller._sent == []


def test_no_delivery_into_rate_limit_banner(controller, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(
        ctl_mod.tmux, "capture_pane",
        lambda p, n: "5-hour limit reached ∙ resets 3pm",
    )
    resets = []
    hits = []
    controller.on_reset = resets.append
    controller.on_limit = hits.append
    controller.store.queue_add("s1", "keep going")
    assert controller.deliver_queued() == []
    assert controller._sent == []
    assert len(resets) == 1  # the observed reset still anchors stats
    assert hits == ["5h"]  # …and the sighting calibrates the observed ceiling


def test_append_items_deliver_as_one_paste(controller, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: "")
    pasted = []
    monkeypatch.setattr(
        ctl_mod.tmux, "paste_text",
        lambda p, t, submit=True: (pasted.append((p, t)) or (True, "")),
    )
    a = controller.store.queue_add("s1", "run the tests")
    b = controller.store.queue_add("s1", "then update the changelog", mode="append")
    assert controller.deliver_queued() == [a.id, b.id]
    # multiline combined → bracketed paste, not send-keys (raw LF would submit early)
    assert pasted == [("%1", "run the tests\nthen update the changelog")]
    assert controller._sent == []
    assert controller.store.queue_next("s1") is None


def test_cooldown_blocks_double_fire(controller, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: "")
    controller.store.queue_add("s1", "one")
    controller.store.queue_add("s1", "two")
    assert len(controller.deliver_queued()) == 1
    assert len(controller.deliver_queued()) == 0  # within cooldown
    controller._queue_sent_at["s1"] = 0.0  # cooldown elapsed
    assert len(controller.deliver_queued()) == 1
    assert [t for _, t in controller._sent] == ["one", "two"]


def test_hold_reason_explains_why_not_delivering(controller, monkeypatch):
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: "❯ plain")
    controller.store.queue_add("s1", "later")
    _live(monkeypatch, status="busy")
    assert controller.queue_hold_reason("s1") == "session is busy, not idle"
    _live(monkeypatch, status="shell")
    assert controller.queue_hold_reason("s1") == "session is shell, not idle"
    _live(monkeypatch, status="idle", waiting_for="permission")
    assert controller.queue_hold_reason("s1") == "waiting for permission"
    _live(monkeypatch, status="idle", pane=None)
    assert controller.queue_hold_reason("s1") == "session isn’t running in tmux"
    _live(monkeypatch, status="idle")
    assert controller.queue_hold_reason("s1") is None  # would deliver


def test_hold_reason_none_when_queue_empty(controller, monkeypatch):
    _live(monkeypatch, status="busy")
    assert controller.queue_hold_reason("s1") is None


def test_deliver_now_overrides_idle_gate(controller, monkeypatch):
    _live(monkeypatch, status="shell")  # would never auto-deliver
    monkeypatch.setattr(ctl_mod.tmux, "capture_pane", lambda p, n: "❯ plain")
    item = controller.store.queue_add("s1", "go anyway")
    assert controller.deliver_queued() == []  # auto path holds
    sent, reason = controller.deliver_now("s1")
    assert sent == [item.id] and reason is None
    assert controller._sent == [("%1", "go anyway")]


def test_deliver_now_still_refuses_a_menu(controller, monkeypatch):
    _live(monkeypatch, status="shell")
    monkeypatch.setattr(
        ctl_mod.tmux, "capture_pane", lambda p, n: "Proceed?\n❯ 1. Yes\n  2. No\n"
    )
    controller.store.queue_add("s1", "1")
    sent, reason = controller.deliver_now("s1")
    assert sent == [] and reason and "menu" in reason
    assert controller._sent == []


def test_deliver_now_nothing_queued(controller, monkeypatch):
    _live(monkeypatch)
    assert controller.deliver_now("s1") == ([], "nothing queued")


# --- REST ------------------------------------------------------------------------


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(queue_router.router)
    app.state.autopilot = type(
        "AP",
        (),
        {
            "store": store,
            "queue_hold_reason": lambda self, sid: None,
            "deliver_now": lambda self, sid: ([1], None),
        },
    )()
    return TestClient(app)


def test_queue_endpoints_roundtrip(client):
    r = client.post("/api/sessions/s9/queue", json={"text": "  after this, run tests  "})
    assert r.status_code == 200
    item = r.json()
    assert item["text"] == "after this, run tests" and item["status"] == "pending"
    view = client.get("/api/sessions/s9/queue").json()
    assert [q["id"] for q in view["items"]] == [item["id"]]
    assert view["hold_reason"] is None
    assert client.delete(f"/api/sessions/s9/queue/{item['id']}").status_code == 200
    assert client.delete(f"/api/sessions/s9/queue/{item['id']}").status_code == 404
    assert client.post("/api/sessions/s9/queue", json={"text": "   "}).status_code == 400


def test_send_now_endpoint(client, store):
    store.queue_add("s9", "x")
    assert client.post("/api/sessions/s9/queue/send-now").json() == {"ok": True, "sent": [1]}
