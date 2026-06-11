"""FastMCP tool definitions for muse.

The user's Claude Code connects to `http://127.0.0.1:<port>/mcp` and calls these
tools to QUERY sessions (read-only) and MARK THEM UP by authoring Investigations
— muse-owned documents that reference real sessions/messages. References are
bidirectional: clickable into the session from muse's UI, surfaced as backlinks
on the session, and readable back here via `get_session_references` so the AI
accumulates a reusable cross-session knowledge base.

The SessionService is shared with the web app (set via `set_service` at startup).
Heavy reads run in a threadpool so MCP calls never stall the event loop that also
serves the UI. `mcp` here is the top-level SDK package (this module is muse.mcp).
"""

from __future__ import annotations

from typing import Optional

import anyio
from mcp.server.fastmcp import FastMCP

from ..config import get_settings

# Set once at app startup; tools resolve the shared service through this.
_service = None


def set_service(service) -> None:
    global _service
    _service = service


def _svc():
    if _service is None:  # pragma: no cover - wiring guard
        raise RuntimeError("muse MCP service not initialised")
    return _service


def _ui_url(path: str) -> str:
    s = get_settings()
    return f"http://{s.host}:{s.port}{path}"


async def _to_thread(fn, *args):
    return await anyio.to_thread.run_sync(fn, *args)


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "muse",
        instructions=(
            "muse exposes AI coding-session transcripts (Claude Code, Codex, Gemini, "
            "opencode) for investigation. Use the read tools to understand HOW an agent "
            "reached a result, then record findings with create_investigation / "
            "add_reference so the user can review them in muse's UI. Reference specific "
            "steps using the [step <id>] anchors from get_session (anchor_uuid). Before "
            "investigating a session, call get_session_references to recall prior findings."
        ),
        stateless_http=True,
        streamable_http_path="/",
    )

    # ---- read tools ----------------------------------------------------
    @mcp.tool()
    async def list_sessions(limit: int = 50) -> str:
        """List recent sessions (most recent first) across all providers. One line
        per session: session_id | provider | msgs | tokens | mtime | title. The
        token figure is real work (input+output+cache-creation, excluding cache
        reads) and, for Claude, already INCLUDES subagent usage. Use
        get_session_tokens for a full breakdown."""
        sessions = await _to_thread(_svc().list_sessions)
        lines = [
            f"{s.session_id} | {s.provider} | {s.message_count} msgs | "
            f"{s.total_tokens or '?'} tok | {s.mtime.isoformat()} | {s.title}"
            for s in sessions[: max(1, limit)]
        ]
        return "\n".join(lines) or "(no sessions)"

    @mcp.tool()
    async def search_sessions(query: str, limit: int = 20) -> str:
        """Full-text search across all sessions, GROUPED by session so one chatty
        session can't monopolize results. One line per session:
        session_id | hits | best anchor_uuid | best snippet (anchor deep-links)."""
        resp = await _to_thread(_svc().search, query, max(1, limit) * 5)
        if not resp.hits:
            return f"(no matches for {query!r}; {resp.indexed_sessions} sessions indexed)"
        grouped: dict[str, dict] = {}
        for h in resp.hits:  # hits are rank-ordered; first per session is the best
            g = grouped.setdefault(h.session_id, {"count": 0, "uuid": h.uuid, "snippet": h.snippet})
            g["count"] += 1
        lines = [
            f"{sid} | {g['count']} hit(s) | {g['uuid'] or '-'} | {g['snippet']}"
            for sid, g in list(grouped.items())[: max(1, limit)]
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def get_session(session_id: str, max_context_tokens: int = 16000) -> str:
        """Get an ordered, token-bounded 'trajectory' digest of a session: user
        prompts, reasoning, tool calls + outcomes, errors, compactions — each tagged
        with a [step <id>] anchor you can cite and reference. The single best tool to
        understand how an agent got to its result."""
        result = await _to_thread(_svc().build_session_digest, session_id, max_context_tokens)
        if result is None:
            return f"(session not found: {session_id})"
        return result.text

    @mcp.tool()
    async def get_session_tokens(session_id: str) -> dict:
        """Token usage AND authoritative cost (USD) for a session. For Claude this
        INCLUDES all subagents (rolled into the session) with a main-vs-subagent split,
        a real/cache token breakdown, per-subagent usage, and muse-computed `cost_usd`
        (priced per the model's real rates, so it's authoritative — don't estimate).
        `total_tokens` = real work (input+output+cache-creation); `total_with_cache_read`
        adds cached re-reads. The answer to 'how many tokens / how much $ did session X
        (and its subagents) use?'."""
        usage = await _to_thread(_svc().get_session_tokens, session_id)
        if usage is None:
            return {"error": f"session not found: {session_id}"}
        return usage.model_dump()

    @mcp.tool()
    async def get_usage_at_anchor(session_id: str, anchor_uuid: str) -> dict:
        """Cumulative spend (tokens + USD) up to and INCLUDING a given step — the
        answer to 'how much did it cost to reach milestone X?'. Pass an `anchor_uuid`
        from get_session (a [<kind> <id>] tag). Merges main + subagent usage by time.
        Claude-only."""
        res = await _to_thread(_svc().get_usage_at_anchor, session_id, anchor_uuid)
        if res is None:
            return {"error": "usage-at-anchor is only available for Claude sessions"}
        return res.model_dump()

    @mcp.tool()
    async def get_usage_timeline(session_id: str, limit: int = 100) -> dict:
        """Cost-over-time: cumulative tokens + USD at each user turn (natural milestone
        boundaries), sampled to `limit` points. Use to see how spend accrued across a
        session. Each point carries an anchor_uuid you can cite. Claude-only."""
        res = await _to_thread(_svc().get_usage_timeline, session_id, limit)
        if res is None:
            return {"error": "usage timeline is only available for Claude sessions"}
        return res.model_dump()

    @mcp.tool()
    async def list_subagents(session_id: str) -> str:
        """List a session's subagents with per-agent tokens, cost, and the spawning
        step (anchor_uuid you can deep-link/cite). Claude-only."""
        subs = await _to_thread(_svc().list_subagents, session_id)
        if subs is None:
            return "(subagent accounting is only available for Claude sessions)"
        if not subs:
            return "(no subagents in this session)"
        return "\n".join(
            f"{s.agent_id} | {s.agent_type} | {s.total_tokens} tok | ${s.cost_usd:.2f} | "
            f"spawn={s.spawn_anchor_uuid or '-'}"
            for s in subs
        )

    @mcp.tool()
    async def get_session_outline(session_id: str, offset: int = 0) -> dict:
        """Cheap skeleton of a session — user turns, subagent spawns, compaction
        boundaries, and errors, each with its anchor. Start here for a big session,
        then drill in with get_step. Bounded and returned inline (never spills); if
        `next_offset` is set, call again with offset=next_offset for the rest."""
        res = await _to_thread(_svc().get_session_outline, session_id, offset)
        return res if res is not None else {"error": f"session not found: {session_id}"}

    @mcp.tool()
    async def get_step(session_id: str, anchor_uuid: str) -> dict:
        """Expand ONE step to its full, untruncated content: message/thinking/system
        text, a compaction summary, or a tool's full input + result. `anchor_uuid` is
        a [<kind> <id>] tag from get_session / an anchor from any other tool."""
        res = await _to_thread(_svc().get_step, session_id, anchor_uuid)
        return res if res is not None else {"error": f"session not found: {session_id}"}

    @mcp.tool()
    async def get_compactions(session_id: str) -> dict:
        """List a session's compaction boundaries with a preview of each auto-summary
        and the summary's anchor. Call get_step(summary_anchor) for a summary in full
        (these carry the narrative of long sessions)."""
        res = await _to_thread(_svc().get_compactions, session_id)
        return res if res is not None else {"error": f"session not found: {session_id}"}

    @mcp.tool()
    async def get_errors(session_id: str, offset: int = 0) -> dict:
        """Every error in a session (tool errors + system/API errors) with full text
        and anchors — first-class, not a truncated tail. Returned inline; if
        `next_offset` is set, call again with offset=next_offset for the rest."""
        res = await _to_thread(_svc().get_errors, session_id, offset)
        return res if res is not None else {"error": f"session not found: {session_id}"}

    @mcp.tool()
    async def get_session_steps(
        session_id: str, offset: int = 0, limit: int = 40, kinds: list[str] | None = None
    ) -> dict:
        """Page through a session's steps in order, returning a bounded window and a
        `next_offset`. `kinds` filters by event kind (user, assistant_text, thinking,
        tool_call, tool_result, subagent, system, lifecycle) plus 'error'/'compaction'.
        Use this to read a large session incrementally instead of one huge get_session;
        per-step text is capped — use get_step(anchor) for a step in full."""
        res = await _to_thread(_svc().get_session_steps, session_id, offset, limit, kinds)
        return res if res is not None else {"error": f"session not found: {session_id}"}

    @mcp.tool()
    async def get_file_changes(session_id: str) -> str:
        """List files the session read/edited/wrote, with operation counts and errors."""
        files = await _to_thread(_svc().get_file_changes, session_id)
        if files is None:
            return f"(session not found: {session_id})"
        if not files:
            return "(no file activity)"
        return "\n".join(
            f"{f.path} | read×{f.read_count} edit×{f.edit_count} write×{f.write_count}"
            f" | {f.error_count} errors"
            for f in files
        )

    @mcp.tool()
    async def get_session_artifacts(session_id: str) -> dict:
        """The session's working-dir notes (NOTES.md, *.md) and durable memory files,
        with bounded head previews + absolute paths. This is the freshest evidence
        that lives OUTSIDE the transcript — often it SUPERSEDES an investigation
        written mid-session (e.g. final interfaces/auth state recorded in NOTES.md).
        Read a returned path in full if a preview isn't enough. Memory is Claude-only."""
        res = await _to_thread(_svc().get_session_artifacts, session_id)
        return res if res is not None else {"error": f"session not found: {session_id}"}

    @mcp.tool()
    async def read_artifact(
        session_id: str, path: str, offset: int = 0, limit: int = 20000
    ) -> dict:
        """Read one of a session's artifact files in full (paginated) — e.g. a
        `results/N/console.log`, `core_config.yaml`, or `NOTES.md` from
        get_session_artifacts. `path` must resolve inside the session's project/memory
        dirs (no escaping). Returns content + size + next_offset; page with offset."""
        res = await _to_thread(_svc().read_artifact, session_id, path, offset, limit)
        return res if res is not None else {"error": f"session not found: {session_id}"}

    @mcp.tool()
    async def get_stats() -> str:
        """Aggregate token/cost usage stats across sessions (Claude provider)."""
        stats = await _to_thread(_svc().get_stats)
        t = stats.totals
        return (
            f"sessions={t.sessions} messages={t.messages} tokens={t.total_tokens} "
            f"cost_usd={t.cost_usd:.2f}\n"
            + "\n".join(f"{m.model}: {m.total_tokens} tok, ${m.cost_usd:.2f}" for m in stats.by_model)
        )

    @mcp.tool()
    async def get_session_references(session_id: str) -> str:
        """Backlinks: investigations that already reference this session (your prior
        findings). Call this before investigating to avoid redoing work. A trailing
        FRESHNESS line flags how many steps the session gained since the newest
        referenced step — a large number means the investigation may be stale."""
        backlinks = await _to_thread(_svc().get_session_references, session_id)
        if not backlinks:
            return "(no investigations reference this session yet)"
        lines = [
            f"{b.investigation_id} | {b.investigation_title} | by {b.author} | "
            f"ref={b.ref.id} anchor={b.ref.anchor_uuid or '-'} | {b.ref.comment or b.ref.label}"
            for b in backlinks
        ]
        fr = await _to_thread(_svc().reference_freshness, session_id)
        if fr and fr.get("steps_after") is not None:
            lines.append(
                f"FRESHNESS: session has {fr['session_steps']} steps; newest referenced "
                f"at step {fr['last_referenced_step']} (+{fr['steps_after']} since"
                f"{' — likely stale, consider refreshing' if fr['steps_after'] >= 50 else ''})"
            )
        return "\n".join(lines)

    # ---- markup (write) tools -----------------------------------------
    @mcp.tool()
    async def create_investigation(
        title: str, body: str = "", refs: Optional[list[dict]] = None
    ) -> dict:
        """Create an Investigation: a markup document (markdown `body`) that records
        a finding. Optionally attach `refs`, a list of {session_id, anchor_uuid?,
        label?, comment?} pointing at the evidence. Returns the new id + a muse URL."""
        try:
            inv = await _to_thread(
                _svc().create_investigation, title, body, "ai", "open", refs
            )
        except ValueError as e:
            return {"error": str(e)}  # bad ref id — fix and retry
        return {"id": inv.id, "title": inv.title, "ref_count": len(inv.refs),
                "url": _ui_url(f"/investigations/{inv.id}")}

    @mcp.tool()
    async def create_retrospective(
        session_id: str, body: str, title: str = "", refs: Optional[list[dict]] = None
    ) -> dict:
        """Save a retrospective (post-mortem) of one session. Recipe: first call
        get_session, get_errors, get_session_health, get_file_changes and (claude)
        get_session_tokens; then structure `body` as markdown with sections
        '## Goal', '## What worked', '## What failed', '## Lessons', '## Follow-ups',
        anchoring claims with `refs` ({session_id, anchor_uuid, comment}). The
        session itself is referenced automatically. Retros appear under the Retros
        tab in muse's Investigations page and as badges on the session."""
        all_refs = [{"session_id": session_id}] + list(refs or [])
        try:
            inv = await _to_thread(
                _svc().create_investigation,
                title or f"Retro: {session_id[:12]}", body, "ai", "open",
                all_refs, "retro",
            )
        except ValueError as e:
            return {"error": str(e)}  # bad ref id — fix and retry
        return {"id": inv.id, "title": inv.title, "kind": "retro",
                "ref_count": len(inv.refs), "url": _ui_url(f"/investigations/{inv.id}")}

    @mcp.tool()
    async def append_to_investigation(investigation_id: str, body: str) -> dict:
        """Append a markdown paragraph to an existing investigation's body."""
        inv = await _to_thread(
            _svc().update_investigation, investigation_id, None, None, None, body
        )
        if inv is None:
            return {"error": f"investigation not found: {investigation_id}"}
        return {"id": inv.id, "url": _ui_url(f"/investigations/{inv.id}")}

    @mcp.tool()
    async def update_investigation(
        investigation_id: str, title: Optional[str] = None,
        body: Optional[str] = None, status: Optional[str] = None,
    ) -> dict:
        """Update an investigation's title, body (full replace), and/or status."""
        inv = await _to_thread(
            _svc().update_investigation, investigation_id, title, body, status, None
        )
        if inv is None:
            return {"error": f"investigation not found: {investigation_id}"}
        return {"id": inv.id, "title": inv.title, "status": inv.status,
                "url": _ui_url(f"/investigations/{inv.id}")}

    @mcp.tool()
    async def add_reference(
        investigation_id: str, session_id: str, anchor_uuid: Optional[str] = None,
        label: str = "", comment: str = "",
    ) -> dict:
        """Attach a reference from an investigation to a session step. `anchor_uuid`
        is a [step <id>] anchor from get_session (deep-links in muse's UI)."""
        try:
            ref = await _to_thread(
                _svc().add_reference, investigation_id, session_id, anchor_uuid, label, comment
            )
        except ValueError as e:
            return {"error": str(e)}  # bad session/anchor id — fix and retry
        if ref is None:
            return {"error": f"investigation not found: {investigation_id}"}
        return {"ref_id": ref.id, "session_id": session_id, "anchor_uuid": anchor_uuid,
                "url": _ui_url(f"/sessions/{session_id}?focus={anchor_uuid}" if anchor_uuid
                               else f"/sessions/{session_id}")}

    @mcp.tool()
    async def list_investigations() -> str:
        """List all investigations and retros: id | kind | author | status | refs | title."""
        invs = await _to_thread(_svc().list_investigations)
        if not invs:
            return "(no investigations yet)"
        return "\n".join(
            f"{i.id} | {i.kind} | {i.author} | {i.status} | {i.ref_count} refs | {i.title}"
            for i in invs
        )

    @mcp.tool()
    async def get_investigation(investigation_id: str) -> str:
        """Read one investigation in full: its body plus all references."""
        inv = await _to_thread(_svc().get_investigation, investigation_id)
        if inv is None:
            return f"(investigation not found: {investigation_id})"
        refs = "\n".join(
            f"  - ref {r.id}: {r.session_id} @ {r.anchor_uuid or '-'} — {r.comment or r.label}"
            for r in inv.refs
        )
        return (f"# {inv.title}  ({inv.author}, {inv.status})\n\n{inv.body}\n\n"
                f"References:\n{refs or '  (none)'}")

    @mcp.tool()
    async def find_sessions_for_file(path_or_name: str) -> str:
        """Every session that ever read/edited/wrote files matching `path_or_name`
        (basename or path substring). Use this when debugging a file to pull up the
        prior sessions that touched it, then get_step their exact edits via the
        returned anchors. One line per file; then per-session op totals."""
        files = await _to_thread(_svc().search_files, path_or_name, 20)
        if not files:
            return f"(no indexed file activity matches {path_or_name!r})"
        out = []
        for f in files[:10]:
            out.append(
                f"{f['file_path']} — {f['session_count']} session(s), "
                f"{f['reads'] or 0}r/{f['edits'] or 0}e/{f['writes'] or 0}w"
                f"{', ' + str(f['errors']) + ' errors' if f['errors'] else ''}"
                f"{', last ' + f['last_ts'][:19] if f['last_ts'] else ''}"
            )
            groups = await _to_thread(_svc().file_activity, f["file_path"])
            for g in groups[:5]:
                anchors = [o["tool_use_id"] for o in g["ops"] if o["tool_use_id"]][:3]
                out.append(
                    f"  - {g['session_id']} ({g['title'] or '?'}): "
                    f"{g['reads']}r/{g['edits']}e/{g['writes']}w"
                    f" | anchors: {', '.join(anchors) or '-'}"
                )
        return "\n".join(out)

    @mcp.tool()
    async def get_related_sessions(session_id: str) -> str:
        """Sessions related to this one (same project, shared edited files, temporal
        adjacency), with the shared files as the explanation. Use to find prior or
        parallel work on the same code before re-investigating."""
        rel = await _to_thread(_svc().get_related_sessions, session_id)
        if not rel:
            return "(no related sessions found)"
        lines = []
        for r in rel:
            s = r["summary"]
            shared = f" | shared: {', '.join(r['shared_files'][:3])}" if r["shared_files"] else ""
            lines.append(
                f"{s.session_id} | score {r['score']} | {s.title}{shared}"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def get_session_health(session_id: str) -> dict:
        """Deterministic failure patterns for a session: retry loops (same tool
        banged ≥3× with errors), error spirals (≥50% errors in a 10-result window),
        permission-denial clusters, total error count, and an ok|warn|bad score.
        Anchors in the patterns are [step <id>] ids usable with get_step. Feed this
        into create_retrospective."""
        health = await _to_thread(_svc().get_session_health, session_id, True)
        if health is None:
            return {"error": f"session not found: {session_id}"}
        return health

    @mcp.tool()
    async def get_reentry_brief(session_id: str) -> dict:
        """'Where you left off' for a stale session: the last user goal, open
        TodoWrite todos, errors since the last user turn, recently touched files,
        worklog notes ('next' notes are open loops), investigation freshness, and
        the resume command. Call this first when picking a session back up. After
        reading, you may write an improved narrative brief back with
        add_note(kind='brief') so the user sees it in the muse UI."""
        brief = await _to_thread(_svc().build_reentry_brief, session_id)
        if brief is None:
            return {"error": f"session not found: {session_id}"}
        return brief

    # ---- worklog notes -------------------------------------------------
    @mcp.tool()
    async def add_note(
        body: str,
        session_id: Optional[str] = None,
        anchor_uuid: Optional[str] = None,
        kind: str = "note",
    ) -> dict:
        """Add a lightweight worklog note (much lighter than an investigation).
        Optionally attach it to a session and a [step <id>] anchor. `kind` is one of
        'note' (plain worklog entry), 'next' (an open loop / follow-up that surfaces
        in the continue-working rail), or 'brief' (a narrative re-entry summary)."""
        try:
            note = await _to_thread(
                _svc().create_note, body, session_id, anchor_uuid, kind, "ai"
            )
        except ValueError as e:
            return {"error": str(e)}  # bad session/anchor id — fix and retry
        return {"id": note.id, "kind": note.kind, "day": note.day,
                "session_id": note.session_id}

    @mcp.tool()
    async def list_notes(
        session_id: Optional[str] = None, day: Optional[str] = None
    ) -> str:
        """List worklog notes, newest first — filter by session_id and/or day
        (YYYY-MM-DD). Notes record what the user was doing and what's next
        ('next' notes are open loops; 'brief' notes are re-entry summaries)."""
        notes = await _to_thread(_svc().list_notes, session_id, day)
        if not notes:
            return "(no notes)"
        return "\n".join(
            f"{n.id} | {n.day} | {n.kind} | {n.author} | "
            f"{n.session_id or '-'}{('@' + n.anchor_uuid) if n.anchor_uuid else ''} | {n.body}"
            for n in notes
        )

    return mcp
