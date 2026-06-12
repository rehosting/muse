"""Context assembly for muse's headless AI jobs.

Each packer turns service state (FTS hits, journal days, single sessions) into
ONE prompt string under a character budget, built from the same token-bounded
digests the MCP `get_session` tool serves. Digest step ids ride along so the
model can cite `/sessions/<id>?focus=<uuid>` deep links per ai/prompts.py.

Budgets are characters, not tokens: digests are anchor-dense (~2.5 chars/token),
so 120k chars ≈ 48k tokens — comfortably inside the model window with room for
the answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

ASK_BUDGET_CHARS = 120_000
ASK_MAX_SESSIONS = 6
DAY_BUDGET_CHARS = 100_000
SESSION_BUDGET_CHARS = 60_000

# Digest token caps derived from a char budget (digest runs ~2.5 chars/token).
_CHARS_PER_TOKEN = 2.5


def _digest_block(service, session_id: str, char_share: int) -> Optional[str]:
    summaries = {s.session_id: s for s in service.list_sessions()}
    s = summaries.get(session_id)
    digest = service.build_session_digest(
        session_id, max_context_tokens=max(2000, int(char_share / _CHARS_PER_TOKEN))
    )
    if digest is None:
        return None
    title = s.title if s else session_id
    cwd = s.project_cwd if s else ""
    when = s.mtime.astimezone().strftime("%Y-%m-%d") if s else ""
    header = f"=== SESSION {session_id} | {title} | {cwd} | {when} ==="
    return f"{header}\n{digest.text}"


def pack_for_ask(service, question: str, char_budget: int = ASK_BUDGET_CHARS) -> str:
    """FTS search → top distinct sessions → digests under the budget → question."""
    hits = service.search(question, limit=30).hits
    seen: list[str] = []
    for h in hits:
        if h.session_id not in seen:
            seen.append(h.session_id)
        if len(seen) >= ASK_MAX_SESSIONS:
            break
    blocks: list[str] = []
    if seen:
        share = char_budget // len(seen)
        for sid in seen:
            block = _digest_block(service, sid, share)
            if block:
                blocks.append(block)
    context = "\n\n".join(blocks) if blocks else "(no sessions matched the question)"
    return (
        f"Context — digests of the user's most relevant sessions:\n\n{context}\n\n"
        f"Question: {question}"
    )


def pack_for_session(
    service, session_id: str, char_budget: int = SESSION_BUDGET_CHARS
) -> Optional[str]:
    """One full-budget digest + the re-entry brief's structured signals."""
    block = _digest_block(service, session_id, char_budget)
    if block is None:
        return None
    parts = [block]
    brief = service.build_reentry_brief(session_id) or {}
    todos = brief.get("open_todos") or []
    errors = brief.get("open_errors") or []
    if todos:
        parts.append("Open todos:\n" + "\n".join(f"- {t}" for t in todos[:12]))
    if errors:
        parts.append(
            "Unresolved errors:\n" + "\n".join(f"- {str(e)[:200]}" for e in errors[:8])
        )
    return "\n\n".join(parts)


def _pack_days(service, days: list[str], char_budget: int) -> Optional[str]:
    """Digests + notes for a list of YYYY-MM-DD local days (shared budget)."""
    sessions: list = []
    notes_md: list[str] = []
    for day in days:
        journal = service.get_journal(day)
        sessions.extend(journal["sessions"])
        for n in journal["notes"]:
            notes_md.append(f"- [{day}] ({n.kind}) {n.body}")
    if not sessions:
        return None
    # Newest-first, dedupe (a session can span days), cap the fan-out.
    seen: list[str] = []
    for s in sorted(sessions, key=lambda s: s.mtime, reverse=True):
        if s.session_id not in seen:
            seen.append(s.session_id)
    seen = seen[:10]
    share = char_budget // max(1, len(seen))
    blocks = [b for sid in seen if (b := _digest_block(service, sid, share))]
    parts = []
    if notes_md:
        parts.append("The user's own notes:\n" + "\n".join(notes_md[:40]))
    parts.append("\n\n".join(blocks))
    return "\n\n".join(parts)


def pack_for_day(
    service, day: str, char_budget: int = DAY_BUDGET_CHARS
) -> Optional[str]:
    packed = _pack_days(service, [day], char_budget)
    if packed is None:
        return None
    return f"Context — the sessions and notes of {day}:\n\n{packed}"


def pack_for_week(
    service, week_start: str, char_budget: int = DAY_BUDGET_CHARS
) -> Optional[str]:
    start = datetime.strptime(week_start, "%Y-%m-%d")
    days = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    packed = _pack_days(service, days, char_budget)
    if packed is None:
        return None
    return f"Context — the sessions and notes of the week starting {week_start}:\n\n{packed}"
