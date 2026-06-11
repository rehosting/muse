"""Tests for the alerts watcher fire path (prime → transition/error → notify)."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from muse import alerts, paths
from muse.models import AlertRules, NotifyConfig, NotifyResult, SessionSummary


class FakeService:
    def __init__(self, summaries):
        self.summaries = summaries
        self.sent = []
        self.cfg = NotifyConfig(enabled=True, topic="t")
        self.rules = AlertRules()

    def get_alert_rules(self):
        return self.rules

    def get_notify_config(self):
        return self.cfg

    def list_sessions(self):
        return self.summaries

    def send_notification(self, message, **kw):
        self.sent.append((message, kw))
        return NotifyResult(ok=True, detail="HTTP 200")


def _summary(state):
    return SessionSummary(
        session_id="s", project_dir="-p", title="My sess",
        mtime=datetime.now(timezone.utc), state=state,
    )


def _setup(tmp_path, monkeypatch, first_line):
    projects = tmp_path / "projects"
    proj = projects / "-p"
    proj.mkdir(parents=True)
    f = proj / "s.jsonl"
    f.write_text(json.dumps(first_line) + "\n")
    monkeypatch.setattr(paths, "get_settings", lambda: SimpleNamespace(projects_dir=projects))
    return f


def test_prime_then_error_fires(tmp_path, monkeypatch):
    f = _setup(tmp_path, monkeypatch, {"type": "user", "uuid": "u1", "message": {"content": "hi"}})
    svc = FakeService([_summary("live")])
    w = alerts.AlertsWatcher(svc)

    asyncio.run(w._tick())  # prime — no alerts for existing content
    assert svc.sent == []

    with open(f, "a") as fh:
        fh.write(json.dumps({"isApiErrorMessage": True, "content": "overloaded_error"}) + "\n")
    asyncio.run(w._tick())

    assert len(svc.sent) == 1
    assert "error" in svc.sent[0][0].lower()
    assert w.recent_log()[0].kind == "error" and w.recent_log()[0].delivered


def test_waiting_transition_fires_once(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"type": "user", "uuid": "u1", "message": {"content": "hi"}})
    summ = [_summary("live")]
    svc = FakeService(summ)
    w = alerts.AlertsWatcher(svc)

    asyncio.run(w._tick())  # prime (live)
    summ[0] = _summary("waiting")
    asyncio.run(w._tick())  # live -> waiting fires
    asyncio.run(w._tick())  # still waiting, no refire

    waits = [s for s in svc.sent if "waiting" in s[0].lower()]
    assert len(waits) == 1


def test_disabled_delivery_logs_but_does_not_send(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"type": "user", "uuid": "u1", "message": {"content": "hi"}})
    summ = [_summary("live")]
    svc = FakeService(summ)
    svc.cfg = NotifyConfig(enabled=False, topic="")  # delivery off
    w = alerts.AlertsWatcher(svc)

    asyncio.run(w._tick())
    summ[0] = _summary("waiting")
    asyncio.run(w._tick())

    assert svc.sent == []  # never attempted delivery
    assert w.recent_log() and w.recent_log()[0].delivered is False  # but recorded
