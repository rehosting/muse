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
        # Daily cap on AI spend from autopilot's "ai" idle mode (USD). The mode
        # types AI-drafted replies into live sessions, so it must be bounded;
        # ≤0 disables ai mode entirely (drafts/diagnoses stay available — those
        # are human-initiated).
        self.ai_daily_budget_usd: float = float(
            os.environ.get("MUSE_AI_DAILY_BUDGET_USD", "2.0")
        )

        # --- remote access -------------------------------------------------------
        # Bearer/cookie token for non-loopback clients. Sourced from the env or
        # ~/.muse/auth_token (auto-generated when binding non-loopback). With no
        # token and a loopback bind, auth is entirely permissive (local default).
        self._auth_token_env: str | None = os.environ.get("MUSE_AUTH_TOKEN") or None
        self.auth_allow_loopback: bool = os.environ.get(
            "MUSE_AUTH_ALLOW_LOOPBACK", "1"
        ) not in ("0", "false", "no")
        # Public base URL (e.g. http://devbox.tailnet.ts.net:8848) for links that
        # leave this machine: ntfy click-throughs, MCP-cited UI urls.
        self.public_url: str | None = (
            os.environ.get("MUSE_PUBLIC_URL", "").rstrip("/") or None
        )

    def resolve_auth_token(self) -> str | None:
        """Env token, else ~/.muse/auth_token, generated iff binding non-loopback."""
        from .auth import load_or_create_token

        non_loopback = self.host not in ("127.0.0.1", "localhost", "::1")
        return load_or_create_token(
            self._auth_token_env, self.db_path.parent, generate=non_loopback
        )

    @property
    def base_url(self) -> str:
        """Where links that leave this machine should point. MUSE_PUBLIC_URL is
        the knob for remote setups (the host:port fallback is wrong by
        definition when binding 0.0.0.0)."""
        return self.public_url or f"http://{self.host}:{self.port}"

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
