"""Temporary file drop (see muse/uploads.py).

POST a file from any browser on the tailnet and get back its absolute path on this
machine — the thing you actually paste into an agent. GET lists what's still there,
DELETE removes one early; otherwise the TTL sweep handles it.

The handlers are sync `def` on purpose: FastAPI runs those in a threadpool, so
streaming a 60 MB video to disk never blocks the event loop (and with it every live
pane poll).
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import uploads
from ..config import get_settings
from ..models import UploadList

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _envelope(files: list[dict], errors: list[str] | None = None) -> UploadList:
    s = get_settings()
    return UploadList(
        root=str(uploads.root()),
        ttl_hours=s.upload_ttl_hours,
        max_mb=s.upload_max_mb,
        files=files,  # type: ignore[arg-type]
        errors=errors or [],
    )


@router.get("", response_model=UploadList)
def list_uploads() -> UploadList:
    return _envelope(uploads.list_files())


@router.post("", response_model=UploadList)
def upload(files: list[UploadFile] = File(...)) -> UploadList:
    """Accept one or more files; respond with the saved paths.

    A partial failure (one oversized video in a multi-select) keeps the files that did
    land and names the one that didn't, rather than failing the whole batch — the
    successful paths are already on disk and already useful. Only an entirely failed
    batch becomes an error status.
    """
    saved: list[dict] = []
    errors: list[str] = []
    for f in files:
        try:
            saved.append(uploads.save(f.filename or "upload", f.file))
        except uploads.UploadTooLarge as exc:
            errors.append(str(exc))
        except OSError as exc:
            errors.append(f"{f.filename or 'upload'}: could not write ({exc})")
    if not saved:
        raise HTTPException(status_code=413 if errors else 400,
                            detail="; ".join(errors) or "no files in the request")
    return _envelope(saved, errors)


@router.delete("/{name}")
def delete_upload(name: str) -> dict:
    if not uploads.delete(name):
        raise HTTPException(status_code=404, detail="no such upload")
    return {"ok": True}
