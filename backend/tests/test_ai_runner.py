"""Tests for ClaudeRunner against a fixture shell script standing in for the
claude binary (the real CLI is never invoked)."""

import json
import os
import stat

import pytest

from muse.ai.runner import ClaudeRunner, RunnerError

ENVELOPE = {
    "type": "result",
    "is_error": False,
    "result": "pong",
    "total_cost_usd": 0.001175,
    "duration_ms": 7755,
    "usage": {"input_tokens": 171, "output_tokens": 101},
    "session_id": "fake",
}


def _fake_bin(tmp_path, body: str) -> str:
    p = tmp_path / "fake-claude"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


def test_happy_path_parses_envelope(tmp_path):
    bin_path = _fake_bin(tmp_path, f"cat > /dev/null\necho '{json.dumps(ENVELOPE)}'\n")
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    res = r.run("ping", system="sys", model="haiku", timeout=10)
    assert res.text == "pong"
    assert res.cost_usd == 0.001175
    assert res.duration_ms == 7755
    assert res.usage["output_tokens"] == 101


def test_prompt_arrives_on_stdin(tmp_path):
    out = tmp_path / "captured.txt"
    env = dict(ENVELOPE)
    bin_path = _fake_bin(
        tmp_path, f"cat > {out}\necho '{json.dumps(env)}'\n"
    )
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    r.run("the full packed context", system="s", model="m", timeout=10)
    assert out.read_text() == "the full packed context"


def test_nonzero_exit_raises_with_stderr_tail(tmp_path):
    bin_path = _fake_bin(tmp_path, "cat > /dev/null\necho 'auth expired' >&2\nexit 1\n")
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    with pytest.raises(RunnerError, match="exited 1.*auth expired"):
        r.run("x", system="s", model="m", timeout=10)


def test_garbage_json_raises(tmp_path):
    bin_path = _fake_bin(tmp_path, "cat > /dev/null\necho 'not json'\n")
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    with pytest.raises(RunnerError, match="unparseable"):
        r.run("x", system="s", model="m", timeout=10)


def test_is_error_envelope_raises(tmp_path):
    env = dict(ENVELOPE, is_error=True, result="rate limited")
    bin_path = _fake_bin(tmp_path, f"cat > /dev/null\necho '{json.dumps(env)}'\n")
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    with pytest.raises(RunnerError, match="rate limited"):
        r.run("x", system="s", model="m", timeout=10)


def test_empty_result_raises(tmp_path):
    env = dict(ENVELOPE, result="")
    bin_path = _fake_bin(tmp_path, f"cat > /dev/null\necho '{json.dumps(env)}'\n")
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    with pytest.raises(RunnerError, match="empty result"):
        r.run("x", system="s", model="m", timeout=10)


def test_timeout_kills_process(tmp_path):
    bin_path = _fake_bin(tmp_path, "cat > /dev/null\nsleep 30\n")
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    with pytest.raises(RunnerError, match="timed out"):
        r.run("x", system="s", model="m", timeout=1)
    # The process group was killed — no fake-claude survivors.
    assert r._proc is None


def test_missing_binary(tmp_path):
    r = ClaudeRunner(str(tmp_path / "does-not-exist"), tmp_path / "wd")
    assert not r.available()
    with pytest.raises(RunnerError, match="not found"):
        r.run("x", system="s", model="m", timeout=5)


def test_cost_field_tolerated_when_absent(tmp_path):
    env = {k: v for k, v in ENVELOPE.items() if k != "total_cost_usd"}
    bin_path = _fake_bin(tmp_path, f"cat > /dev/null\necho '{json.dumps(env)}'\n")
    r = ClaudeRunner(bin_path, tmp_path / "wd")
    res = r.run("x", system="s", model="m", timeout=10)
    assert res.cost_usd is None and res.text == "pong"


def test_workdir_created_and_used(tmp_path):
    wd = tmp_path / "ai-wd"
    marker = tmp_path / "cwd.txt"
    bin_path = _fake_bin(
        tmp_path, f"cat > /dev/null\npwd > {marker}\necho '{json.dumps(ENVELOPE)}'\n"
    )
    r = ClaudeRunner(bin_path, wd)
    r.run("x", system="s", model="m", timeout=10)
    assert os.path.realpath(marker.read_text().strip()) == os.path.realpath(str(wd))
