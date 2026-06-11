"""Incremental JSONL reading for append-only transcripts.

Transcripts only ever grow (lines are appended). Re-parsing the whole file on
every poll is wasteful once a session is large, so callers cache a byte offset
and ask only for the lines appended since then.
"""

from __future__ import annotations

from pathlib import Path

import orjson


def new_objects(path: Path, offset: int) -> tuple[list[dict], int]:
    """Parse complete JSON lines appended after `offset`.

    Returns (objects, new_offset) where new_offset is advanced only past the last
    complete (newline-terminated) line — a partial trailing line is left for the
    next call, exactly like the live tailer.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
    except OSError:
        return [], offset
    if not chunk:
        return [], offset
    nl = chunk.rfind(b"\n")
    if nl == -1:
        return [], offset  # no complete line yet
    consumed = chunk[: nl + 1]
    objs: list[dict] = []
    for line in consumed.split(b"\n"):
        if not line.strip():
            continue
        try:
            objs.append(orjson.loads(line))
        except orjson.JSONDecodeError:
            continue
    return objs, offset + len(consumed)
