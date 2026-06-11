"""Provider registry — resolves which adapter owns a session id."""

from __future__ import annotations

from .base import Provider
from .claude_code import ClaudeProvider
from .codex import CodexProvider
from .gemini import GeminiProvider
from .opencode import OpenCodeProvider

# Order matters: prefixed providers are checked before the unprefixed default.
_CLAUDE = ClaudeProvider()
_PROVIDERS: list[Provider] = [
    CodexProvider(),
    GeminiProvider(),
    OpenCodeProvider(),
    _CLAUDE,
]


def providers() -> list[Provider]:
    return _PROVIDERS


def provider_for(session_id: str) -> Provider:
    for p in _PROVIDERS:
        if p.owns(session_id):
            return p
    return _CLAUDE  # default: unprefixed ids are Claude
