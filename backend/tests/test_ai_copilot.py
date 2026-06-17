"""Tests for the AI copilot layer: draft sanitizer, triage parsing, packers."""

import pytest

from muse.ai import context as ai_context
from muse.ai.runner import RunnerError
from muse.services.session_service import _split_triage_block, sanitize_draft


# --- sanitize_draft -----------------------------------------------------------

def test_sanitizer_passes_plain_text():
    assert sanitize_draft("Looks good — fix the test and continue.") == (
        "Looks good — fix the test and continue."
    )


def test_sanitizer_strips_fences():
    assert sanitize_draft("```\ncontinue with step 2\n```") == "continue with step 2"
    assert sanitize_draft("```text\nyes, do it\n```") == "yes, do it"


def test_sanitizer_rejects_slash_and_bang_leaders():
    # Injection via the transcript could smuggle slash commands / bash mode.
    assert sanitize_draft("/compact") == "compact"
    assert sanitize_draft("!rm -rf /") == "rm -rf /"
    assert sanitize_draft("/ ! /clear now") == "clear now"


def test_sanitizer_caps_length():
    assert len(sanitize_draft("x" * 5000)) == 1500


def test_sanitizer_raises_on_empty():
    with pytest.raises(RunnerError):
        sanitize_draft("```\n```")
    with pytest.raises(RunnerError):
        sanitize_draft("   ")


# --- triage block parsing -------------------------------------------------------

def test_triage_block_parses():
    text = 'preamble\n```triage\n{"sid-1": "needs: pick option A", "sid-2": "needs: review"}\n```'
    assert _split_triage_block(text) == {
        "sid-1": "needs: pick option A",
        "sid-2": "needs: review",
    }


def test_triage_block_lenient_on_garbage():
    assert _split_triage_block("no block here") == {}
    assert _split_triage_block("```triage\nnot json\n```") == {}
    assert _split_triage_block('```triage\n["a list"]\n```') == {}


def test_triage_block_truncates_long_lines():
    text = '```triage\n{"s": "' + "y" * 500 + '"}\n```'
    assert len(_split_triage_block(text)["s"]) == 200


# --- packers (fake service, mirrors test_ai_context style) -----------------------

class FakeThreadItem:
    def __init__(self, role, text):
        self.role = role
        self.text = text


class FakeService:
    def __init__(self, digest_text="[U 1] step", items=()):
        self._digest = digest_text
        self._items = list(items)

    def list_sessions(self):
        from types import SimpleNamespace
        from datetime import datetime

        return [SimpleNamespace(session_id="s1", title="T", project_cwd="/p",
                                mtime=datetime.fromisoformat("2026-06-10T12:00:00+00:00"))]

    def build_session_digest(self, sid, max_context_tokens=16000):
        from muse.ai.digest import DigestResult

        return DigestResult(text=self._digest) if self._digest else None

    def build_reentry_brief(self, sid):
        return {"open_todos": ["ship it"]}

    def get_thread(self, sid):
        from types import SimpleNamespace

        return SimpleNamespace(items=self._items)

    def get_session_health(self, sid):
        return {
            "retry_loops": [{"tool": "Bash", "times": 4, "label": "make build"}],
            "error_spirals": [],
            "permission_denials": [],
            "error_count": 7,
        }


def test_pack_for_reply_includes_style_and_pane():
    items = [
        FakeThreadItem("user", "fix the parser"),
        FakeThreadItem("assistant", "done"),
        FakeThreadItem("user", "<command-name>/compact</command-name>"),  # skipped
        FakeThreadItem("user", "now add tests"),
    ]
    svc = FakeService(items=items)
    out = ai_context.pack_for_reply(svc, "s1", pane_text="  $ waiting for input  ")
    assert "=== SESSION s1 |" in out
    assert "> fix the parser" in out and "> now add tests" in out
    assert "<command-name>" not in out.split("recent replies")[1].split("Current terminal")[0]
    assert "waiting for input" in out
    assert "Open todos:\n- ship it" in out


def test_pack_for_diagnose_includes_patterns():
    out = ai_context.pack_for_diagnose(FakeService(), "s1")
    assert "retry loop: Bash ×4" in out
    assert "total errors: 7" in out


def test_pack_for_triage_batches_and_caps():
    svc = FakeService()
    out = ai_context.pack_for_triage(svc, ["s1"] * 12)
    assert out.count("=== SESSION") == 8  # capped
    assert ai_context.pack_for_triage(svc, []) is None
