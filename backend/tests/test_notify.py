"""Tests for outbound push notifications (ntfy) and config store."""

from muse import notify
from muse.alerts import _scan_errors
from muse.models import AlertRules, NotifyConfig


def test_disabled_or_unconfigured_does_not_send(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: calls.append(1))

    assert notify.send(NotifyConfig(enabled=False, topic="t"), "hi").ok is False
    assert notify.send(NotifyConfig(enabled=True, topic=""), "hi").ok is False
    assert calls == []  # never hit the network


def test_send_builds_request_and_reports_ok(monkeypatch):
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)

    cfg = NotifyConfig(enabled=True, server="https://ntfy.sh", topic="muse-secret", priority=4)
    res = notify.send(cfg, "all done", title="muse", tags="white_check_mark", click="http://x")
    assert res.ok and "200" in res.detail
    assert captured["url"] == "https://ntfy.sh/muse-secret"
    assert captured["data"] == b"all done"
    assert captured["headers"]["title"] == "muse"
    assert captured["headers"]["priority"] == "4"
    assert captured["headers"]["tags"] == "white_check_mark"
    assert captured["headers"]["click"] == "http://x"


def test_store_roundtrip(tmp_path):
    store = notify.NotifyStore(tmp_path / "muse.db")
    assert store.get_config().enabled is False  # default
    saved = store.set_config(NotifyConfig(enabled=True, topic="abc", priority=5))
    assert saved.topic == "abc"
    reloaded = store.get_config()
    assert reloaded.enabled and reloaded.topic == "abc" and reloaded.priority == 5
    # rules persist independently of config
    assert store.get_rules().on_waiting is True  # default
    store.set_rules(AlertRules(on_waiting=False, on_stopped=True, poll_seconds=30))
    r = store.get_rules()
    assert r.on_waiting is False and r.on_stopped is True and r.poll_seconds == 30
    store.close()


def test_scan_errors_detects_each_kind():
    objs = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
        {"isApiErrorMessage": True, "content": "overloaded_error"},
        {"type": "system", "level": "error", "content": "boom"},
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "is_error": True, "content": "cmd failed"}]
            },
        },
    ]
    errs = _scan_errors(objs)
    assert len(errs) == 3
    assert any("API error" in e for e in errs)
    assert any("System error" in e for e in errs)
    assert any("Tool error" in e and "cmd failed" in e for e in errs)
    assert _scan_errors([{"type": "assistant"}]) == []
