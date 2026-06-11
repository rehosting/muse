"""Detection of Claude Code's <persisted-output> wrapper for large tool results.

When a tool result is too large to inline, the transcript stores a string like:

    <persisted-output>
    Output too large (180.4KB). Full output saved to: /abs/.../tool-results/<id>.txt

    Preview (first 2KB):
    <preview text...>

The full content lives in the referenced .txt file; we serve it lazily.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_SAVED_RE = re.compile(r"Full output saved to:\s*(?P<path>\S+)")
_PREVIEW_RE = re.compile(r"Preview \(first[^)]*\):\s*\n", re.IGNORECASE)


@dataclass
class PersistedOutput:
    cache_id: str
    preview: str


def detect_persisted(content: str) -> Optional[PersistedOutput]:
    """Return persisted-output info if `content` is a persisted wrapper, else None."""
    if "<persisted-output>" not in content:
        return None
    saved = _SAVED_RE.search(content)
    if not saved:
        return None
    path = saved.group("path")
    cache_id = path.rsplit("/", 1)[-1]
    if cache_id.endswith(".txt"):
        cache_id = cache_id[: -len(".txt")]

    preview = ""
    m = _PREVIEW_RE.search(content)
    if m:
        preview = content[m.end():]
        preview = preview.replace("</persisted-output>", "").strip()
    return PersistedOutput(cache_id=cache_id, preview=preview)
