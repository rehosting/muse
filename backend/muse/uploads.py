"""Temporary file drop: a browser (usually the phone) → this machine's /tmp.

The deliverable of an upload is a **path**, not a URL. The point is to hand a file to
an agent running in a tmux pane — "read /tmp/muse-uploads-1002/screenshot.png" — so
everything here optimises for a short, stable, pasteable absolute path rather than for
serving the bytes back out.

Files live under /tmp, not ~/.muse: they are hand-off scraps, not muse state, and the
OS clearing them on reboot is exactly the lifetime we want. A TTL sweep covers the rest.

This is the one place muse accepts bytes from a client, so the rules are narrow:
a client-supplied name is reduced to a plain basename, every write is streamed under a
size cap, and anything that resolves back out of the root is refused.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Optional
from uuid import uuid4

from .config import get_settings

# Long enough to stay recognisable, short enough to stay pasteable.
MAX_NAME = 80
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_CHUNK = 1 << 20


class UploadTooLarge(Exception):
    """Raised mid-write once an upload passes the configured cap."""


def root() -> Path:
    """The drop dir, created 0700 on first use."""
    d = get_settings().upload_dir
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def max_bytes() -> int:
    return get_settings().upload_max_mb * 1024 * 1024


def safe_name(raw: str) -> str:
    """Reduce a client-supplied filename to a plain, shell-friendly basename.

    Browsers send whatever the OS handed them, which on a phone can be a full path or
    odd unicode, and from a crafted client can be '../../.bashrc'. Only the last
    component survives, only characters that don't need quoting survive within it, and
    a leading '.' is stripped so an upload can never land as a dotfile or as '..'.
    """
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    name = _UNSAFE_RE.sub("_", name)
    name = name.lstrip(".") or "upload"
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = f"{stem[: max(1, MAX_NAME - len(ext) - 1)]}.{ext}"
        else:
            name = name[:MAX_NAME]
    return name


def _split_ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, ext) if dot else (name, "")


def unique_path(name: str) -> Path:
    """A free path for `name`, suffixing -2, -3 … rather than overwriting.

    Two photos really can arrive as IMG_0001.jpg, and silently replacing the first one
    would destroy a file the user may already have pasted a path to.
    """
    d = root()
    if not (d / name).exists():
        return d / name
    stem, ext = _split_ext(name)
    suffix = f".{ext}" if ext else ""
    for i in range(2, 1000):
        cand = d / f"{stem}-{i}{suffix}"
        if not cand.exists():
            return cand
    return d / f"{stem}-{uuid4().hex[:8]}{suffix}"


def entry(p: Path) -> dict:
    st = p.stat()
    return {
        "name": p.name,
        "path": str(p),
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def save(filename: str, stream: BinaryIO, limit: Optional[int] = None) -> dict:
    """Stream one upload to disk, returning its entry (the `path` is the point).

    The cap is enforced while writing, never from Content-Length: that header is
    client-supplied, and trusting it is how a "small" upload fills the disk. A write
    that trips the cap — or dies halfway — takes its partial file with it.
    """
    cap = max_bytes() if limit is None else limit
    target = unique_path(safe_name(filename))
    written = 0
    try:
        with target.open("wb") as fh:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > cap:
                    raise UploadTooLarge(
                        f"{target.name} is larger than the {cap // (1024 * 1024)} MB limit"
                    )
                fh.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    target.chmod(0o600)
    return entry(target)


def sweep(ttl_hours: Optional[int] = None) -> int:
    """Delete uploads older than the TTL; returns how many went.

    Called on list/save rather than from a timer — the dir only matters when someone
    is looking at it, and a background job for this would be a loop that exists to
    delete empty space.
    """
    ttl = get_settings().upload_ttl_hours if ttl_hours is None else ttl_hours
    if ttl <= 0:
        return 0
    cutoff = time.time() - ttl * 3600
    removed = 0
    for p in root().iterdir():
        try:
            if p.is_file() and not p.is_symlink() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def list_files() -> list[dict]:
    """Everything currently in the drop dir, newest first, after a TTL sweep."""
    sweep()
    out: list[dict] = []
    for p in root().iterdir():
        try:
            if p.is_file() and not p.is_symlink():
                out.append(entry(p))
        except OSError:
            continue
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def resolve(name: str) -> Optional[Path]:
    """Map a client-supplied name back onto a real file in the root, or None.

    Sanitising the name is not enough on its own: the *resolved* path's parent is
    compared with the resolved root, so a symlink sitting in the dir can't redirect a
    delete at something outside it.
    """
    d = root().resolve()
    try:
        target = (d / safe_name(name)).resolve()
    except OSError:
        return None
    if target.parent != d or not target.is_file():
        return None
    return target


def delete(name: str) -> bool:
    target = resolve(name)
    if target is None:
        return False
    try:
        target.unlink()
    except OSError:
        return False
    return True
