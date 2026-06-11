"""Filesystem path helpers for the ~/.claude/projects layout.

Layout (all read-only):
    projects/{encoded-cwd}/{sessionId}.jsonl                       main transcript
    projects/{encoded-cwd}/{sessionId}/subagents/agent-{id}.jsonl  subagent transcript
    projects/{encoded-cwd}/{sessionId}/subagents/agent-{id}.meta.json
    projects/{encoded-cwd}/{sessionId}/tool-results/{id}.txt       persisted tool output
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import get_settings


def encode_cwd(abs_path: str) -> str:
    """Encode an absolute path the way Claude Code names project dirs.

    '/home/luke/workspace/muse' -> '-home-luke-workspace-muse'
    """
    return abs_path.replace("/", "-")


def decode_cwd(dir_name: str) -> str:
    """Best-effort decode of a project dir name back to a path.

    This is lossy (a literal '-' in a path is indistinguishable from a '/'),
    so prefer the `cwd` field stored inside the JSONL for anything that must be
    correct. This is only used as a directory label fallback.
    """
    return "/" + dir_name.lstrip("-").replace("-", "/")


@dataclass(frozen=True)
class SessionPaths:
    project_dir: str
    session_id: str

    @property
    def root(self) -> Path:
        return get_settings().projects_dir / self.project_dir

    @property
    def jsonl(self) -> Path:
        return self.root / f"{self.session_id}.jsonl"

    @property
    def session_dir(self) -> Path:
        return self.root / self.session_id

    @property
    def subagents_dir(self) -> Path:
        return self.session_dir / "subagents"

    @property
    def tool_results_dir(self) -> Path:
        return self.session_dir / "tool-results"

    def subagent_jsonl(self, agent_id: str) -> Path:
        return self.subagents_dir / f"{_agent_filename(agent_id)}.jsonl"

    def subagent_meta(self, agent_id: str) -> Path:
        return self.subagents_dir / f"{_agent_filename(agent_id)}.meta.json"

    def tool_result_file(self, cache_id: str) -> Path:
        return self.tool_results_dir / f"{cache_id}.txt"


def _agent_filename(agent_id: str) -> str:
    """Subagent files are named agent-{id}; accept ids with or without prefix."""
    return agent_id if agent_id.startswith("agent-") else f"agent-{agent_id}"


def find_session(session_id: str) -> Optional[SessionPaths]:
    """Locate a session's transcript across all project dirs."""
    projects = get_settings().projects_dir
    if not projects.is_dir():
        return None
    for project_dir in projects.iterdir():
        if not project_dir.is_dir():
            continue
        if (project_dir / f"{session_id}.jsonl").is_file():
            return SessionPaths(project_dir=project_dir.name, session_id=session_id)
    return None
