"""Runtime configuration for muse.

Everything is read-only with respect to ~/.claude. The only knob most users need
is CLAUDE_DIR, which defaults to ~/.claude.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _opt_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


class Settings:
    """Process-wide settings, sourced from environment variables."""

    def __init__(self) -> None:
        self.claude_dir: Path = Path(
            os.environ.get("MUSE_CLAUDE_DIR", str(Path.home() / ".claude"))
        ).expanduser()
        # OpenAI Codex CLI transcripts (read-only), for multi-provider support.
        self.codex_dir: Path = Path(
            os.environ.get("MUSE_CODEX_DIR", str(Path.home() / ".codex"))
        ).expanduser()
        # Gemini CLI transcripts (read-only).
        self.gemini_dir: Path = Path(
            os.environ.get("MUSE_GEMINI_DIR", str(Path.home() / ".gemini"))
        ).expanduser()
        # opencode transcripts (read-only). Newer opencode stores everything in a
        # single SQLite DB (opencode.db) under this dir, not per-session files.
        self.opencode_dir: Path = Path(
            os.environ.get(
                "MUSE_OPENCODE_DIR", str(Path.home() / ".local" / "share" / "opencode")
            )
        ).expanduser()
        # muse's OWN database — annotations (renames, bookmarks) live here, never
        # in ~/.claude, which muse treats as strictly read-only.
        self.db_path: Path = Path(
            os.environ.get("MUSE_DB_PATH", str(Path.home() / ".muse" / "muse.db"))
        ).expanduser()
        self.host: str = os.environ.get("MUSE_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("MUSE_PORT", "8848"))
        # A session whose transcript was modified within this many seconds is
        # considered "running" for the purposes of the UI's live indicator.
        self.running_threshold_seconds: int = int(
            os.environ.get("MUSE_RUNNING_THRESHOLD_SECONDS", "30")
        )
        # A session idle longer than this (and not actively live) is "stopped";
        # an awaiting-user session more recent than this is "waiting".
        self.stopped_threshold_seconds: int = int(
            os.environ.get("MUSE_STOPPED_THRESHOLD_SECONDS", "1800")
        )
        # Live tailing uses polling (robust against inotify exhaustion, which is
        # common on dev boxes running Claude Code). Latency ~= this delay.
        self.poll_delay_ms: int = int(os.environ.get("MUSE_POLL_DELAY_MS", "500"))
        # Optional spend budgets per window (USD). When set, the stats page draws
        # a budget/pace line so usage can be compared against the window's limit.
        self.limit_5h_usd: float | None = _opt_float(os.environ.get("MUSE_LIMIT_5H_USD"))
        self.limit_week_usd: float | None = _opt_float(os.environ.get("MUSE_LIMIT_WEEK_USD"))
        # --- AI layer (headless `claude -p`) -----------------------------------
        # Jobs share the user's Max-plan auth + 5h window, so the defaults are
        # conservative: cheap-ish model, one job at a time, auto-digests off.
        self.ai_claude_bin: str = os.environ.get("MUSE_AI_CLAUDE_BIN", "claude")
        self.ai_model: str = os.environ.get("MUSE_AI_MODEL", "sonnet")
        self.ai_timeout_seconds: int = int(os.environ.get("MUSE_AI_TIMEOUT_SECONDS", "300"))
        self.ai_auto_digest: bool = os.environ.get("MUSE_AI_AUTO_DIGEST", "") in (
            "1", "true", "yes",
        )

    @property
    def ai_workdir(self) -> Path:
        """Dedicated cwd for headless claude runs. Even with
        --no-session-persistence the CLI drops a tiny ai-title stub transcript
        under ~/.claude/projects/<encoded-cwd>/ — running from here lets the
        session list filter those out by project_cwd."""
        return self.db_path.parent / "ai"

    @property
    def projects_dir(self) -> Path:
        return self.claude_dir / "projects"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
