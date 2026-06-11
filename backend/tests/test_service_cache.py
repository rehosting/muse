"""TTL caching of the expensive read endpoints in SessionService."""

import pytest

from muse import stats
from muse.config import get_settings
from muse.services import session_service
from muse.services.session_service import SessionService
from muse.services.events import EventBroker
from muse.models import StatsResponse


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # Never open the real ~/.muse/muse.db from tests — the live server holds it.
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _service():
    return SessionService(EventBroker())


def test_list_sessions_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    class FakeProvider:
        def iter_sessions(self):
            calls["n"] += 1
            return []

    # Isolate from real provider data (~/.claude, ~/.codex, ~/.gemini).
    monkeypatch.setattr(session_service, "providers", lambda: [FakeProvider()])
    svc = _service()
    svc.list_sessions()
    svc.list_sessions()
    svc.list_sessions()
    assert calls["n"] == 1  # subsequent calls served from cache


def test_get_stats_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_compute(*args):
        calls["n"] += 1
        from datetime import datetime, timezone
        from muse.models import Totals, WindowStat

        return StatsResponse(
            generated_at=datetime.now(timezone.utc),
            totals=Totals(),
            hours=WindowStat(label="5h", window_seconds=1),
            week=WindowStat(label="week", window_seconds=1),
        )

    monkeypatch.setattr(stats, "compute_stats", fake_compute)
    svc = _service()
    svc.get_stats()
    svc.get_stats()
    assert calls["n"] == 1


def test_stats_recomputes_after_ttl(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(stats, "compute_stats", lambda *a: calls.__setitem__("n", calls["n"] + 1) or _empty_stats())
    svc = _service()
    svc.get_stats()
    # Force the cache to look stale, then call again.
    ts, result = svc._stats_caches[0]
    svc._stats_caches[0] = (ts - 999, result)
    svc.get_stats()
    assert calls["n"] == 2


def _empty_stats():
    from datetime import datetime, timezone
    from muse.models import Totals, WindowStat

    return StatsResponse(
        generated_at=datetime.now(timezone.utc),
        totals=Totals(),
        hours=WindowStat(label="5h", window_seconds=1),
        week=WindowStat(label="week", window_seconds=1),
    )
