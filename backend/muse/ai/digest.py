"""Build a compact, ordered "trajectory" digest of a session.

This is the output of the MCP `get_session` tool: a token-bounded plaintext
reconstruction of HOW an agent reached its result — user prompts, reasoning,
tool calls + outcomes, errors, compactions — in order, with `[step <id>]`
anchors so the model can cite a specific step and the muse UI can deep-link to
it via the existing ?focus=<uuid> viewer param.

Driven from the already-flattened `SessionEvent` timeline (same order the UI
renders), so a citation lines up with what the user sees.

Deterministic: same inputs → byte-identical output (no clock/randomness).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import FileChange, SessionEvent, Thread

# The digest is ANCHOR-DENSE: nearly every line carries a 36-char uuid
# (`[U <uuid>]`) that tokenizes to ~14 tokens, so the text runs ~2.5 chars/token
# — far denser than prose (~4). Budgeting at 4 under-counted tokens by ~1.6x and
# let a "bounded" digest blow past the client's per-tool-result token cap. Use the
# real density so the char budget maps to the intended token budget.
_CHARS_PER_TOKEN = 2.5
_BUDGET_FRACTION = 0.75

# Per-step character caps (errors get more — they're usually the point).
_CAP_USER = 800
_CAP_THINK = 600
_CAP_TEXT = 600
_CAP_RESULT_OK = 300
_CAP_RESULT_ERR = 1200
_CAP_LABEL = 200

_MAX_FILE_LINES = 40
_MAX_ERROR_LINES = 30

# Absolute ceiling on the rendered digest, regardless of max_context_tokens. MCP
# clients (e.g. Claude Code, default MAX_MCP_OUTPUT_TOKENS=25k) spill a tool result
# that exceeds their per-result token cap to a file and make the caller page it back
# — the exact failure we're avoiding. The digest is anchor-dense (~2.5 chars/token),
# so we cap on an estimated OUTPUT token count (not raw chars) and leave a wide margin
# under 25k so it returns inline even if a client lowers the limit. ~12k tokens.
_OUTPUT_TOKEN_CAP = 12000
# A hair under the token cap (margin for the rounding in _estimate_tokens) so the
# final text — footer included — always estimates at or below _OUTPUT_TOKEN_CAP.
_HARD_CAP_CHARS = int(_OUTPUT_TOKEN_CAP * _CHARS_PER_TOKEN) - 200  # ~29.8k chars
_NAV_HINT = (
    "navigate with get_session_outline (skeleton), get_step(anchor) for one step "
    "in full, get_compactions / get_errors, or get_session_steps(offset,limit) to page."
)


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate for the anchor-dense digest (~2.5 chars/token)."""
    return int(len(text) / _CHARS_PER_TOKEN) + 1


@dataclass
class _Step:
    anchor: Optional[str]  # full uuid/tool_use_id, or None for dividers
    text: str  # rendered line WITHOUT the [step id] prefix
    prefix: str  # one-letter kind tag: U/T/A/K/S/G, or "" for dividers
    must_keep: bool = False


@dataclass
class DigestResult:
    text: str
    steps: dict[str, str] = field(default_factory=dict)  # shortId -> full uuid
    truncated: bool = False
    step_count: int = 0


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    head = int(n * 0.7)
    tail = n - head
    return f"{text[:head]} … [truncated {len(text) - n} chars] … {text[-tail:]}"


def _render_steps(events: list[SessionEvent]) -> list[_Step]:
    steps: list[_Step] = []
    last_assistant_idx = -1
    for ev in events:
        anchor = ev.anchor_uuid or ev.tool_use_id
        kind = ev.kind
        if ev.is_compaction:
            steps.append(_Step(None, f"--- COMPACTION: {ev.label or 'context compacted'} ---",
                               "", must_keep=True))
        elif kind == "user":
            steps.append(_Step(anchor, f"USER: {_truncate(ev.detail or ev.label, _CAP_USER)}",
                               "U", must_keep=True))
        elif kind == "thinking":
            steps.append(_Step(anchor, f"THINK: {_truncate(ev.detail or ev.label, _CAP_THINK)}", "T"))
        elif kind == "assistant_text":
            steps.append(_Step(anchor, f"SAY: {_truncate(ev.detail or ev.label, _CAP_TEXT)}", "A"))
            last_assistant_idx = len(steps) - 1
        elif kind == "tool_call":
            steps.append(_Step(anchor, f"TOOL {_truncate(ev.label, _CAP_LABEL)}", "K"))
        elif kind == "tool_result":
            cap = _CAP_RESULT_ERR if ev.is_error else _CAP_RESULT_OK
            outcome = "ERROR" if ev.is_error else (ev.status or "ok")
            dur = f" ({ev.duration_ms}ms)" if ev.duration_ms else ""
            steps.append(_Step(anchor, f"RESULT {outcome}{dur}: {_truncate(ev.label, cap)}",
                               "K", must_keep=ev.is_error))
        elif kind == "subagent":
            steps.append(_Step(anchor, f"SUBAGENT: {_truncate(ev.label, _CAP_LABEL)}",
                               "G", must_keep=True))
        elif kind == "system":
            is_err = ev.is_error or (ev.level == "error")
            body = _truncate(ev.detail or ev.label, _CAP_TEXT)
            steps.append(_Step(anchor, f"SYSTEM{'(error)' if is_err else ''}: {body}",
                               "S", must_keep=is_err))
        # lifecycle and empty events are dropped (noise)
    if last_assistant_idx >= 0:  # the final assistant turn anchors "how it concluded"
        steps[last_assistant_idx].must_keep = True
    return steps


def _line(step: _Step) -> str:
    # The step id IS the full anchor uuid/tool_use_id, so an id the model cites
    # (e.g. add_reference(anchor_uuid=...)) deep-links directly via ?focus=.
    if not step.prefix or not step.anchor:
        return step.text
    return f"[{step.prefix} {step.anchor}] {step.text}"


def _files_section(file_changes: list[FileChange]) -> list[str]:
    if not file_changes:
        return []
    lines = ["", "== FILES TOUCHED =="]
    for fc in file_changes[:_MAX_FILE_LINES]:
        parts = []
        if fc.read_count:
            parts.append(f"read×{fc.read_count}")
        if fc.edit_count:
            parts.append(f"edit×{fc.edit_count}")
        if fc.write_count:
            parts.append(f"write×{fc.write_count}")
        if fc.error_count:
            parts.append(f"({fc.error_count} error)")
        lines.append(f"{fc.path}  {' '.join(parts)}".rstrip())
    if len(file_changes) > _MAX_FILE_LINES:
        lines.append(f"… and {len(file_changes) - _MAX_FILE_LINES} more files")
    return lines


def _errors_section(steps: list[_Step]) -> list[str]:
    err_steps = [s for s in steps if (s.prefix == "K" and s.text.startswith("RESULT ERROR"))
                 or (s.prefix == "S" and s.text.startswith("SYSTEM(error)"))]
    if not err_steps:
        return []
    lines = ["", "== ERRORS (chronological) =="]
    for s in err_steps[:_MAX_ERROR_LINES]:
        lines.append(f"[{s.prefix} {s.anchor or '-'}] {s.text[:200]}")
    if len(err_steps) > _MAX_ERROR_LINES:
        lines.append(f"… and {len(err_steps) - _MAX_ERROR_LINES} more errors")
    return lines


def build_digest(
    thread: Thread,
    events: list[SessionEvent],
    file_changes: list[FileChange],
    *,
    max_context_tokens: int = 16000,
) -> DigestResult:
    char_budget = int(max_context_tokens * _CHARS_PER_TOKEN * _BUDGET_FRACTION)
    steps = _render_steps(events)
    anchors = [s.anchor for s in steps if s.anchor]

    n_user = sum(1 for s in steps if s.prefix == "U")
    n_tool = sum(1 for s in steps if s.prefix == "K" and s.text.startswith("TOOL"))
    n_err = sum(1 for s in steps if s.text.startswith("RESULT ERROR")
                or s.text.startswith("SYSTEM(error)"))
    n_compact = sum(1 for s in steps if s.prefix == "" and s.text.startswith("--- COMPACTION"))

    header = [
        f"SESSION DIGEST (provider={thread.provider} model={thread.model or '?'} "
        f"steps={len(steps)})",
        f"Project: {thread.project_cwd or '?'}",
        f"Title: {thread.title}",
        f"Trajectory: {n_user} user prompts, {n_tool} tool calls, {n_err} errors, "
        f"{n_compact} compactions",
        "",
        "Legend: each line is tagged [<kind> <id>] where kind is U=user "
        "T=thinking A=assistant K=tool call/result S=system G=subagent. Pass a "
        "line's <id> as anchor_uuid to add_reference to deep-link that exact step.",
        "",
        "== TRAJECTORY ==",
    ]
    files = _files_section(file_changes)
    errors = _errors_section(steps)
    fixed = header + ["", "<<TRAJECTORY>>", ""] + files + errors
    fixed_chars = sum(len(line) + 1 for line in fixed) - len("<<TRAJECTORY>>")
    traj_budget = max(0, char_budget - fixed_chars)

    # Decide which trajectory lines fit. Must-keep always included; fill the rest
    # with a head+tail window over the fillable steps (stalls cluster near the
    # re-prompt; the tail holds the most recent reasoning).
    rendered = [(s, _line(s)) for s in steps]
    must = [(i, ln) for i, (s, ln) in enumerate(rendered) if s.must_keep or not s.anchor]
    fill = [(i, ln) for i, (s, ln) in enumerate(rendered) if not (s.must_keep or not s.anchor)]
    must_chars = sum(len(ln) + 1 for _, ln in must)

    keep_idx = {i for i, _ in must}
    truncated = False
    remaining = traj_budget - must_chars
    if remaining < 0:
        truncated = True  # even must-keep overflows; we still emit them (bounded enough)
    else:
        # Greedily take from the head and tail of `fill` alternately until budget runs out.
        head, tail = 0, len(fill) - 1
        take_head = True
        while head <= tail and remaining > 0:
            idx, ln = fill[head] if take_head else fill[tail]
            cost = len(ln) + 1
            if cost > remaining:
                break
            keep_idx.add(idx)
            remaining -= cost
            if take_head:
                head += 1
            else:
                tail -= 1
            take_head = not take_head
        if head <= tail:
            truncated = True

    traj_lines: list[str] = []
    elided = 0
    for i, (_s, ln) in enumerate(rendered):
        if i in keep_idx:
            if elided:
                traj_lines.append(f"... [{elided} steps elided to fit budget] ...")
                elided = 0
            traj_lines.append(ln)
        else:
            elided += 1
    if elided:
        traj_lines.append(f"... [{elided} steps elided to fit budget] ...")

    body_lines = header + traj_lines + files + errors
    text = "\n".join(body_lines)
    # Hard ceiling: never emit a digest big enough to spill to disk on the client.
    # Cap on estimated OUTPUT tokens (the client's actual limit), not raw chars.
    if _estimate_tokens(text) > _OUTPUT_TOKEN_CAP:
        footer = (
            f"\n\n… [digest capped near {_OUTPUT_TOKEN_CAP} tokens so it returns "
            f"inline; {_NAV_HINT}]"
        )
        text = text[: _HARD_CAP_CHARS - len(footer)] + footer  # leave room for the footer
        truncated = True
    # Step ids ARE the full anchors, so the map is identity — kept for callers
    # that want the set of citable anchors without re-parsing the text.
    return DigestResult(
        text=text,
        steps={a: a for a in anchors},
        truncated=truncated,
        step_count=len(steps),
    )
