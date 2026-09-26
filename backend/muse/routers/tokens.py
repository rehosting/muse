"""Token-usage analytics, sourced from the tokentracker CLI (see muse/tokentracker.py).

The payload is passed through largely as the tool produced it rather than remapped into
muse's own shapes — its schema is the contract, and translating it here would only add a
layer to keep in sync. muse contributes the envelope: which window, when it was taken,
and whether the numbers came from the tool's cache or a full re-parse.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from .. import tokentracker

router = APIRouter(prefix="/api/tokens", tags=["tokens"])

# Windows the UI offers. Bounded so a stray ?days=100000 can't ask the CLI to chew
# through every transcript on disk mid-request.
ALLOWED_DAYS = (1, 7, 30, 90)


@router.get("")
def usage(
    days: int = Query(7, description="trailing window in days"),
    refresh: bool = Query(False, description="re-parse transcripts (slow) instead of the CLI cache"),
) -> dict:
    if days not in ALLOWED_DAYS:
        raise HTTPException(
            status_code=400, detail=f"days must be one of {', '.join(map(str, ALLOWED_DAYS))}"
        )
    try:
        data = tokentracker.get_usage(days=days, refresh=refresh)
    except tokentracker.TrackerError as exc:
        # 503, not 500: the tool being absent or slow is an environment condition the
        # page should render as a message, not an app crash.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "days": days,
        "refreshed": refresh,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "available": bool(data.get("available")),
        "session_count": data.get("session_count", 0),
        "summary": data.get("summary") or {},
        "by_model": data.get("by_model") or [],
        "subagents": data.get("subagents") or [],
        "sessions": data.get("sessions") or [],
        "provenance": data.get("provenance") or {},
    }
