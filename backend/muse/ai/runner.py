"""Headless `claude -p` runner — muse's only path to an LLM.

Uses the user's installed Claude Code CLI (Max-plan OAuth auth; no API key to
manage), one blocking subprocess per call. Every invocation is locked down:
no tools, no slash commands, no MCP, no settings — a single-shot text
completion over the prompt we pipe in on stdin.

Verified live against claude CLI 2.1.173 (2026-06-11):
- `--output-format json` envelope fields: type/subtype, is_error, result,
  total_cost_usd (POPULATED even on subscription auth), duration_ms,
  num_turns, session_id, usage{input_tokens, output_tokens,
  cache_creation_input_tokens, cache_read_input_tokens}, modelUsage,
  permission_denials, uuid. Unknown/missing fields must be tolerated —
  the envelope drifts across CLI versions.
- Prompt on stdin works with `-p` (no argv length limits for ~100KB contexts).
- `--no-session-persistence` suppresses the conversation transcript BUT a
  ~100-byte stub jsonl (a single `ai-title` line) still lands under
  ~/.claude/projects/<encoded-cwd>/. That's why we always run from a dedicated
  muse-owned cwd (~/.muse/ai) and the session list filters that cwd out —
  the cwd filter is REQUIRED, not belt-and-suspenders.
- NEVER use `--bare`: it reads auth strictly from ANTHROPIC_API_KEY and
  bypasses the OAuth/keychain path entirely.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class RunnerError(RuntimeError):
    """A claude -p invocation failed (bad exit, is_error, or unparseable output)."""


@dataclass
class RunnerResult:
    text: str
    cost_usd: Optional[float] = None
    usage: dict = field(default_factory=dict)
    duration_ms: Optional[int] = None
    raw: dict = field(default_factory=dict)


class ClaudeRunner:
    """One-shot headless claude calls. Thread-safe for the single-worker queue:
    at most one subprocess lives at a time; `cancel()` kills it from another
    thread (the API handler)."""

    def __init__(self, claude_bin: str, workdir: Path) -> None:
        self.claude_bin = claude_bin
        self.workdir = workdir
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()

    def available(self) -> bool:
        return shutil.which(self.claude_bin) is not None

    def cancel(self) -> bool:
        """Kill the in-flight subprocess (if any). The run() call then raises."""
        with self._proc_lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def run(
        self,
        prompt: str,
        system: str,
        model: str,
        timeout: int = 300,
    ) -> RunnerResult:
        if not self.available():
            raise RunnerError(f"claude binary not found: {self.claude_bin!r}")
        self.workdir.mkdir(parents=True, exist_ok=True)
        argv = [
            self.claude_bin,
            "-p",
            "--output-format", "json",
            "--model", model,
            "--no-session-persistence",
            "--setting-sources", "",
            "--tools", "",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--system-prompt", system,
        ]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.workdir),
            text=True,
            start_new_session=True,  # own process group → killable as a unit
        )
        with self._proc_lock:
            self._proc = proc
        try:
            try:
                stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait()
                raise RunnerError(f"claude -p timed out after {timeout}s") from None
        finally:
            with self._proc_lock:
                self._proc = None
        if proc.returncode != 0:
            tail = (stderr or "").strip()[-500:]
            raise RunnerError(
                f"claude -p exited {proc.returncode}"
                + (f" — stderr: {tail}" if tail else "")
            )
        try:
            envelope = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as e:
            raise RunnerError(
                f"unparseable claude -p output: {e} — head: {(stdout or '')[:300]!r}"
            ) from None
        if envelope.get("is_error"):
            raise RunnerError(
                f"claude -p reported an error: {str(envelope.get('result'))[:500]}"
            )
        text = envelope.get("result")
        if not isinstance(text, str) or not text.strip():
            raise RunnerError("claude -p returned an empty result")
        cost = envelope.get("total_cost_usd")
        return RunnerResult(
            text=text,
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            usage=envelope.get("usage") or {},
            duration_ms=envelope.get("duration_ms"),
            raw=envelope,
        )
