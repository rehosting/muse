"""Tests for AI context packing (fake service; no transcripts, no claude)."""

from datetime import datetime
from types import SimpleNamespace

from muse.ai import context as ai_context
from muse.ai.digest import DigestResult
from muse.services.session_service import _split_refs_block


def _summary(sid: str, title: str = "t", day: str = "2026-06-10"):
    return SimpleNamespace(
        session_id=sid,
        title=title,
        project_cwd=f"/proj/{sid}",
        mtime=datetime.fromisoformat(f"{day}T12:00:00+00:00"),
    )


class FakeService:
    def __init__(self, sessions, hits=(), digests=None, notes=()):
        self._sessions = sessions
        self._hits = list(hits)
        self._digests = digests or {}
        self._notes = list(notes)
        self.digest_calls = []

    def list_sessions(self):
        return self._sessions

    def search(self, q, limit=30):
        return SimpleNamespace(hits=self._hits)

    def build_session_digest(self, sid, max_context_tokens=16000):
        self.digest_calls.append((sid, max_context_tokens))
        text = self._digests.get(sid)
        return DigestResult(text=text) if text else None

    def build_reentry_brief(self, sid):
        return {"open_todos": ["finish X"], "open_errors": ["boom"]}

    def get_journal(self, day):
        return {
            "day": day,
            "notes": self._notes,
            "sessions": [s for s in self._sessions
                         if s.mtime.astimezone().strftime("%Y-%m-%d") == day],
        }


def test_pack_for_ask_dedupes_and_headers():
    hits = [SimpleNamespace(session_id="a"), SimpleNamespace(session_id="a"),
            SimpleNamespace(session_id="b")]
    svc = FakeService(
        [_summary("a", "Alpha"), _summary("b", "Beta")],
        hits=hits,
        digests={"a": "[U 111] did a thing", "b": "[U 222] did another"},
    )
    out = ai_context.pack_for_ask(svc, "what broke?")
    assert out.count("=== SESSION a |") == 1  # deduped
    assert "=== SESSION b | Beta | /proj/b | 2026-06-10 ===" in out
    assert out.rstrip().endswith("Question: what broke?")


def test_pack_for_ask_caps_sessions_and_splits_budget():
    hits = [SimpleNamespace(session_id=f"s{i}") for i in range(10)]
    svc = FakeService(
        [_summary(f"s{i}") for i in range(10)],
        hits=hits,
        digests={f"s{i}": f"digest {i}" for i in range(10)},
    )
    ai_context.pack_for_ask(svc, "q", char_budget=60_000)
    assert len(svc.digest_calls) == ai_context.ASK_MAX_SESSIONS
    # Budget split across the chosen sessions, converted to digest tokens.
    share_tokens = svc.digest_calls[0][1]
    assert share_tokens == int((60_000 / 6) / 2.5)


def test_pack_for_ask_no_hits_still_asks():
    svc = FakeService([], hits=[])
    out = ai_context.pack_for_ask(svc, "anything?")
    assert "(no sessions matched the question)" in out
    assert "Question: anything?" in out


def test_pack_for_session_includes_brief_signals():
    svc = FakeService([_summary("a")], digests={"a": "[U 1] x"})
    out = ai_context.pack_for_session(svc, "a")
    assert "=== SESSION a |" in out
    assert "Open todos:\n- finish X" in out
    assert "Unresolved errors:\n- boom" in out


def test_pack_for_session_unknown_returns_none():
    svc = FakeService([], digests={})
    assert ai_context.pack_for_session(svc, "nope") is None


def test_pack_for_day_uses_journal_sessions_and_notes():
    note = SimpleNamespace(kind="note", body="tried the WAL fix")
    svc = FakeService(
        [_summary("a", day="2026-06-10"), _summary("b", day="2026-06-09")],
        digests={"a": "d-a", "b": "d-b"},
        notes=[note],
    )
    out = ai_context.pack_for_day(svc, "2026-06-10")
    assert "sessions and notes of 2026-06-10" in out
    assert "=== SESSION a |" in out
    assert "=== SESSION b |" not in out  # different day
    assert "tried the WAL fix" in out


def test_pack_for_day_empty_returns_none():
    svc = FakeService([])
    assert ai_context.pack_for_day(svc, "2026-01-01") is None


def test_pack_for_week_spans_days():
    svc = FakeService(
        [_summary("mon", day="2026-06-08"), _summary("sun", day="2026-06-14"),
         _summary("out", day="2026-06-15")],
        digests={"mon": "d", "sun": "d", "out": "d"},
    )
    out = ai_context.pack_for_week(svc, "2026-06-08")
    assert "=== SESSION mon |" in out and "=== SESSION sun |" in out
    assert "=== SESSION out |" not in out


def test_split_refs_block():
    text = (
        "## Retro\nstuff happened\n\n```refs\n"
        '[{"session_id": "s1", "anchor_uuid": "u1", "label": "fix"},'
        ' {"label": "no sid — dropped"}]\n```\n'
    )
    body, refs = _split_refs_block(text)
    assert body == "## Retro\nstuff happened"
    assert refs == [{"session_id": "s1", "anchor_uuid": "u1", "label": "fix"}]


def test_split_refs_block_absent_or_garbage():
    assert _split_refs_block("plain answer") == ("plain answer", [])
    body, refs = _split_refs_block("text\n```refs\nnot json\n```")
    assert body == "text" and refs == []
