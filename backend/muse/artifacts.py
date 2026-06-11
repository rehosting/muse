"""Session artifacts: the working-dir notes and durable memory files a session
produced, which often hold the freshest understanding (NOTES.md, memory/*.md) —
evidence that lives OUTSIDE the transcript.

Strictly read-only and bounded: we only stat + read a short head preview of text
files, cap how many we return, and never recurse into large trees. muse never
writes here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_PREVIEW_CHARS = 600
_MAX_PER_GROUP = 10


def _entry(p: Path) -> Optional[dict]:
    try:
        st = p.stat()
    except OSError:
        return None
    if not p.is_file():
        return None
    preview = ""
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            preview = fh.read(_PREVIEW_CHARS)
    except OSError:
        return None
    return {
        "path": str(p),
        "name": p.name,
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "preview": preview,
        "preview_truncated": st.st_size > len(preview),
    }


def _collect(paths: list[Path]) -> list[dict]:
    """Dedupe, newest-first, capped, with previews."""
    seen: set[Path] = set()
    entries: list[dict] = []
    for p in paths:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        e = _entry(p)
        if e:
            entries.append(e)
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries[:_MAX_PER_GROUP]


def collect_notes(cwd: Optional[str]) -> list[dict]:
    """Markdown/text notes in the session's working dir. Top-level *.md/*.txt plus
    NOTES.md up to two levels deep (depth-limited so we never crawl a whole repo)."""
    if not cwd:
        return []
    d = Path(cwd)
    if not d.is_dir():
        return []
    found: list[Path] = []
    try:
        found += sorted(d.glob("*.md")) + sorted(d.glob("*.txt"))
        found += sorted(d.glob("*/NOTES.md")) + sorted(d.glob("*/*/NOTES.md"))
    except OSError:
        return []
    return _collect(found)


def collect_memory(memory_dir: Optional[Path]) -> list[dict]:
    """Durable memory files for the session's project (Claude's per-project memory)."""
    if memory_dir is None or not memory_dir.is_dir():
        return []
    try:
        return _collect(sorted(memory_dir.glob("*.md")))
    except OSError:
        return []


_MAX_RESULTS_DIRS = 8
_MAX_RESULT_FILES = 40


def _run_sort_key(p: Path):
    """Numbered run dirs (results/0, results/1, …) sort numerically; otherwise by mtime."""
    try:
        return (1, int(p.name), 0.0)
    except ValueError:
        try:
            return (0, 0, p.stat().st_mtime)
        except OSError:
            return (0, 0, 0.0)


def collect_results(cwd: Optional[str]) -> list[dict]:
    """Out-of-band evidence the session produced: `results/` run dirs (e.g. a tool's
    `results/N/` outputs — console logs, configs, health files). These are written by
    subprocesses, NOT the Write/Edit tools, so transcript file-change tracking misses
    them. Returns, per results dir, a MANIFEST of the LATEST run (no content — files
    can be hundreds of KB; use read_artifact to pull one)."""
    if not cwd:
        return []
    base = Path(cwd)
    if not base.is_dir():
        return []
    dirs: set[Path] = set()
    try:
        for pat in ("results", "*/results", "*/*/results", "*/*/*/results"):
            dirs |= {d for d in base.glob(pat) if d.is_dir()}
    except OSError:
        return []
    # Resolve each results dir's latest run, then order dirs by that run's recency so
    # the target the session was actually working on surfaces first (and survives the
    # caller's budget trim) instead of whichever sorts first alphabetically.
    latest_by_dir: list[tuple[Path, Path, float]] = []
    for rd in dirs:
        try:
            runs = [p for p in rd.iterdir() if p.is_dir()]
        except OSError:
            continue
        if not runs:
            continue
        latest = max(runs, key=_run_sort_key)
        try:
            mtime = latest.stat().st_mtime
        except OSError:
            mtime = 0.0
        latest_by_dir.append((rd, latest, mtime))
    latest_by_dir.sort(key=lambda t: t[2], reverse=True)

    out: list[dict] = []
    for rd, latest, _mtime in latest_by_dir[:_MAX_RESULTS_DIRS]:
        files = []
        try:
            run_count = sum(1 for p in rd.iterdir() if p.is_dir())
            for f in sorted(latest.iterdir()):
                if not f.is_file():
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                files.append({"path": str(f), "name": f.name, "size": st.st_size})
        except OSError:
            continue
        out.append({
            "results_dir": str(rd),
            "run_count": run_count,
            "latest_run": str(latest),
            "files": files[:_MAX_RESULT_FILES],
            "files_truncated": len(files) > _MAX_RESULT_FILES,
        })
    return out


def _within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def read_artifact(allowed_roots: list[Path], path: str, offset: int, limit: int) -> dict:
    """Read a bounded slice of one artifact file, but ONLY if it resolves inside one of
    `allowed_roots` (the session's cwd / memory dir) — so a relative or ../ path can't
    escape to arbitrary files. Returns content + size + next_offset (paginated)."""
    roots = [r.resolve() for r in allowed_roots if r]
    if not roots:
        return {"error": "session has no resolvable project directory"}
    raw = Path(path)
    candidate = raw if raw.is_absolute() else roots[0] / raw
    try:
        target = candidate.resolve()
    except OSError:
        return {"error": f"bad path: {path}"}
    if not any(_within(target, r) for r in roots):
        return {"error": f"path is outside the session's project/memory dirs: {path}"}
    if not target.is_file():
        return {"error": f"not a file: {path}"}
    limit = max(1, min(limit, 40000))
    offset = max(0, offset)
    try:
        size = target.stat().st_size
        with target.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read(limit)
    except OSError as e:
        return {"error": f"read failed: {e}"}
    text = chunk.decode("utf-8", errors="replace")
    new_offset = offset + len(chunk)
    return {
        "path": str(target),
        "size": size,
        "offset": offset,
        "content": text,
        "next_offset": new_offset if new_offset < size else None,
    }
