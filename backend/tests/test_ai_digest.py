"""Tests for the trajectory digest builder (the MCP get_session output)."""

from muse.ai.digest import (
    _OUTPUT_TOKEN_CAP,
    _estimate_tokens,
    build_digest,
)
from muse.models import FileChange, SessionEvent, Thread


def _ev(i, kind, **kw):
    return SessionEvent(index=i, kind=kind, type="t", **kw)


def _fixture():
    events = [
        _ev(0, "user", detail="Fix the auth bug", anchor_uuid="u1"),
        _ev(1, "thinking", detail="reasoning " * 80, anchor_uuid="t1"),
        _ev(2, "tool_call", label="Read(auth.py)", anchor_uuid="k1", tool_use_id="k1"),
        _ev(3, "tool_result", label="contents ok", anchor_uuid="k1", tool_use_id="k1",
            status="ok", duration_ms=12),
        _ev(4, "tool_call", label="Bash(pytest)", anchor_uuid="k2", tool_use_id="k2"),
        _ev(5, "tool_result", label="FAILED ImportError " * 60, anchor_uuid="k2",
            tool_use_id="k2", is_error=True, duration_ms=900),
        _ev(6, "assistant_text", detail="the import is wrong", anchor_uuid="a1"),
        _ev(7, "system", detail="context compacted", anchor_uuid="c1", is_compaction=True),
        _ev(8, "user", detail="still failing?", anchor_uuid="u2"),
        _ev(9, "assistant_text", detail="final conclusion", anchor_uuid="a2"),
    ]
    thread = Thread(session_id="s", provider="claude", title="Auth bug",
                    model="claude-opus-4-8", project_cwd="/x")
    files = [FileChange(path="auth.py", read_count=1, edit_count=2, error_count=1)]
    return thread, events, files


def test_deterministic():
    th, ev, fc = _fixture()
    assert build_digest(th, ev, fc).text == build_digest(th, ev, fc).text


def test_sections_and_anchors():
    th, ev, fc = _fixture()
    d = build_digest(th, ev, fc, max_context_tokens=16000)
    assert "== TRAJECTORY ==" in d.text
    assert "== FILES TOUCHED ==" in d.text
    assert "== ERRORS (chronological) ==" in d.text
    # every cited [X <shortid>] resolves to a real anchor
    import re
    cited = set(re.findall(r"\[[A-Z] ([0-9a-zA-Z~]+)\]", d.text))
    for sid in cited:
        assert sid in d.steps  # shortId -> full uuid map
    # the error result is present and gets the long cap (not truncated to 300)
    assert "RESULT ERROR" in d.text


def test_error_result_keeps_more_than_ok():
    th, ev, fc = _fixture()
    d = build_digest(th, ev, fc, max_context_tokens=16000)
    err_line = next(line for line in d.text.splitlines() if "RESULT ERROR" in line)
    assert len(err_line) > 400  # got the 1200-char error cap, not the 300 ok cap


def test_must_keep_survives_tiny_budget():
    th, ev, fc = _fixture()
    d = build_digest(th, ev, fc, max_context_tokens=200)
    assert d.truncated is True
    # both user prompts, the error, and the compaction divider always survive
    assert d.text.count("USER:") == 2
    assert "RESULT ERROR" in d.text
    assert "COMPACTION" in d.text
    assert "elided" in d.text  # elision marker present


def test_empty_session():
    th = Thread(session_id="s", provider="claude", title="empty")
    d = build_digest(th, [], [])
    assert "== TRAJECTORY ==" in d.text
    assert d.step_count == 0


def test_huge_session_stays_under_token_cap_no_spill():
    """A huge, anchor-dense session must never produce a digest over the client's
    MCP token cap — otherwise the caller spills it to disk (the bug this guards).
    Even raising max_context_tokens can't blow past the hard token ceiling."""
    th = Thread(session_id="s", provider="claude", title="big", model="m")
    # 4000 must-keep-ish user turns with full uuids — the dense, worst case.
    events = [
        _ev(i, "user", detail=f"prompt number {i} " * 20,
            anchor_uuid=f"{i:08d}-aaaa-bbbb-cccc-1234567890ab")
        for i in range(4000)
    ]
    d = build_digest(th, events, [], max_context_tokens=1_000_000)
    assert _estimate_tokens(d.text) <= _OUTPUT_TOKEN_CAP
    assert d.truncated is True
    # and it tells the caller how to drill in instead of just stopping
    assert "get_session_outline" in d.text
