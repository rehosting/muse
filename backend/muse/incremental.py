"""Incremental JSONL reading for append-only transcripts.

Transcripts only ever grow (lines are appended). Re-parsing the whole file on
every poll is wasteful once a session is large, so callers cache a byte offset
and ask only for the lines appended since then.
"""

from __future__ import annotations

import os
from pathlib import Path

import orjson

# Transcripts above this are pathological (e.g. multi-GB gemini "merged"/dump
# files). Reading gigabytes on a request thread or background tick pegs CPU,
# bloats RSS, and holds the store lock — so every parse path reads only the most
# recent MAX_PARSE_BYTES. The most recent activity is what matters anyway, and
# the cap sits far above any real session (largest legit transcript here ~40MB;
# the junk ones start at 114MB). Tunable via MUSE_MAX_PARSE_BYTES (0 disables).
MAX_PARSE_BYTES = int(os.environ.get("MUSE_MAX_PARSE_BYTES", str(128 * 1024 * 1024)))


def new_objects(path: Path, offset: int, max_bytes: int | None = None) -> tuple[list[dict], int]:
    """Parse complete JSON lines appended after `offset`.

    Returns (objects, new_offset) where new_offset is advanced only past the last
    complete (newline-terminated) line — a partial trailing line is left for the
    next call, exactly like the live tailer.

    If the unread region exceeds `max_bytes` (default MAX_PARSE_BYTES, 0 disables),
    skip ahead and read only the trailing `max_bytes` — bounding cold reads of
    pathological multi-GB files. JSONL is one object per line, so dropping the
    partial line we land inside keeps every parsed object intact.
    """
    cap = MAX_PARSE_BYTES if max_bytes is None else max_bytes
    try:
        with path.open("rb") as fh:
            if cap:
                try:
                    size = os.fstat(fh.fileno()).st_size
                except OSError:
                    size = 0
                if size - offset > cap:
                    fh.seek(size - cap)
                    fh.readline()  # discard the partial line we landed inside
                    offset = fh.tell()
                else:
                    fh.seek(offset)
            else:
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
