"""Hook-driven alert paths on AlertsWatcher: instant fire, settle-and-recheck,
reply cancellation, and tick suppression."""

import asyncio
from datetime import datetime, timezone

import pytest

from muse import alerts
from muse.models import AlertRules, LiveSession, NotifyConfig, NotifyResult, SessionSummary


class FakeService:
    def __init__(self):
        self.sent = []
        self.rules = AlertRules()
        self.cfg = NotifyConfig(enabled=True, topic="t")

    def get_alert_rules(self):
        return self.rules

    def get_notify_config(self):
        return self.cfg

    def list_sessions(self):
        return [SessionSummary(session_id="s", project_dir="-p", title="My sess",
                               mtime=datetime.now(timezone.utc), state="waiting")]

    def send_notification(self, message, **kw):
        self.sent.append((message, kw))
        return NotifyResult(ok=True, detail="HTTP 200")


@pytest.fixture
def watcher(monkeypatch):
    monkeypatch.setattr(alerts, "_TURN_END_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(
        alerts.live_discovery, "discover",
        lambda: [LiveSession(session_id="s", pid=1, status="idle", pane_id="%1")],
    )
    return alerts.AlertsWatcher(FakeService())


def test_needs_you_fires_once_per_pause(watcher):
    asyncio.run(watcher.hook_needs_you("s", "Claude needs your permission to use Bash"))
    asyncio.run(watcher.hook_needs_you("s", "Claude needs your permission to use Bash"))
    assert len(watcher.service.sent) == 1
    title, kw = watcher.service.sent[0]
    assert "needs you" in kw["title"] and "/drive/s" in kw["click"]
    # the polling tick is suppressed for the same transition
    assert watcher._suppressed("s", "waiting") is True


def test_turn_ended_fires_when_still_idle(watcher):
    asyncio.run(watcher.hook_turn_ended("s"))
    assert len(watcher.service.sent) == 1
    assert "ready for you" in watcher.service.sent[0][1]["title"]


def test_turn_ended_cancelled_by_user_reply(watcher, monkeypatch):
    monkeypatch.setattr(alerts, "_TURN_END_SETTLE_SECONDS", 0.05)

    async def scenario():
        t = asyncio.create_task(watcher.hook_turn_ended("s"))
        await asyncio.sleep(0.01)
        watcher.hook_user_replied("s")  # user answered during the settle window
        await t

    asyncio.run(scenario())
    assert watcher.service.sent == []


def test_turn_ended_skipped_when_session_moved_on(watcher, monkeypatch):
    monkeypatch.setattr(
        alerts.live_discovery, "discover",
        lambda: [LiveSession(session_id="s", pid=1, status="busy", pane_id="%1")],
    )
    asyncio.run(watcher.hook_turn_ended("s"))
    assert watcher.service.sent == []


def test_session_end_respects_rule(watcher):
    asyncio.run(watcher.hook_session_end("s"))  # on_stopped defaults False
    assert watcher.service.sent == []
    watcher.service.rules = AlertRules(on_stopped=True)
    asyncio.run(watcher.hook_session_end("s"))
    assert len(watcher.service.sent) == 1
    asyncio.run(watcher.hook_session_end("s"))  # deduped
    assert len(watcher.service.sent) == 1


def test_rules_off_means_silent(watcher):
    watcher.service.rules = AlertRules(on_waiting=False)
    asyncio.run(watcher.hook_needs_you("s", "m"))
    asyncio.run(watcher.hook_turn_ended("s"))
    assert watcher.service.sent == []
