"""Normalized Pydantic models — these *are* the API contract.

The raw on-disk JSONL is messy and evolving; everything the frontend sees goes
through these models so the UI has a single, stable shape to render.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "system", "other"]
BlockKind = Literal["text", "thinking", "tool_use"]
TitleSource = Literal["ai-title", "user", "slug", "none", "custom"]


class Usage(BaseModel):
    """Token accounting for an assistant turn (or a session total)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    service_tier: Optional[str] = None

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens
            + other.cache_read_input_tokens,
            service_tier=self.service_tier or other.service_tier,
        )


class ToolResult(BaseModel):
    """The result of a tool call, paired back to its ToolUse by tool_use_id."""

    tool_use_id: str
    content: Optional[str] = None
    is_error: bool = False
    # When the real output was too large to inline, it lives in a cache file.
    truncated: bool = False
    cache_id: Optional[str] = None
    preview: Optional[str] = None


class SubagentRef(BaseModel):
    """A pointer from a parent tool_use to a subagent transcript on disk."""

    agent_id: str
    agent_type: str
    description: str = ""
    tool_use_id: str


class ToolUse(BaseModel):
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    caller: Optional[dict[str, Any]] = None
    result: Optional[ToolResult] = None
    subagent: Optional[SubagentRef] = None


class ContentBlock(BaseModel):
    kind: BlockKind
    text: Optional[str] = None
    tool_use: Optional[ToolUse] = None


class ThreadItem(BaseModel):
    """One renderable entry in a reconstructed conversation thread."""

    uuid: str
    parent_uuid: Optional[str] = None
    role: Role
    type: str  # original line `type`
    timestamp: Optional[datetime] = None
    blocks: list[ContentBlock] = Field(default_factory=list)
    text: Optional[str] = None  # convenience for plain user text
    usage: Optional[Usage] = None
    model: Optional[str] = None
    is_sidechain: bool = False
    level: Optional[str] = None  # for system entries: info | notice | warning | error


EventKind = Literal[
    "user",
    "assistant_text",
    "thinking",
    "tool_call",
    "tool_result",
    "subagent",
    "system",
    "lifecycle",
]


class SessionEvent(BaseModel):
    """One low-level entry in a session's complete timeline (any JSONL type)."""

    index: int
    kind: EventKind
    type: str  # raw JSONL `type` (e.g. "permission-mode")
    role: Optional[str] = None
    timestamp: Optional[datetime] = None
    label: str = ""
    detail: Optional[str] = None
    anchor_uuid: Optional[str] = None  # conversation node to scroll to
    tool_use_id: Optional[str] = None
    tool_name: Optional[str] = None
    status: Optional[str] = None  # ok | error | truncated | pending (tool results)
    is_error: bool = False
    level: Optional[str] = None
    duration_ms: Optional[int] = None
    subagent: Optional[SubagentRef] = None
    is_compaction: bool = False  # system compact_boundary — render as a divider


class CompactionBoundary(BaseModel):
    """One point where the session's context was compacted/summarized."""

    uuid: Optional[str] = None
    timestamp: Optional[datetime] = None
    trigger: Optional[str] = None  # manual | auto
    pre_tokens: Optional[int] = None  # context size just before compaction
    duration_ms: Optional[int] = None


class SessionLineage(BaseModel):
    """A session's internal lineage: segments split by compaction boundaries."""

    session_id: str
    segment_count: int = 1  # boundaries + 1
    total_pre_tokens: int = 0
    boundaries: list[CompactionBoundary] = Field(default_factory=list)


FileOpKind = Literal["read", "edit", "write"]


class FileOp(BaseModel):
    """A single tool operation against a file, linked back to its tool_use."""

    tool_use_id: str
    kind: FileOpKind
    tool_name: str  # original tool (Read/Edit/MultiEdit/Write/NotebookEdit)
    timestamp: Optional[datetime] = None
    is_error: bool = False
    edit_count: int = 1  # >1 for MultiEdit (number of edit hunks)


class FileChange(BaseModel):
    """Aggregated activity against one file across a session/subagent thread."""

    path: str
    read_count: int = 0
    edit_count: int = 0
    write_count: int = 0
    error_count: int = 0
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    ops: list[FileOp] = Field(default_factory=list)


class Thread(BaseModel):
    """A fully reconstructed session (or subagent) transcript."""

    session_id: str
    provider: str = "claude"  # which tool produced this session (claude | codex | …)
    project_cwd: Optional[str] = None
    version: Optional[str] = None
    title: str
    title_source: TitleSource = "none"
    model: Optional[str] = None  # primary model, when the provider records it
    context_window: Optional[int] = None  # provider-supplied window (e.g. Codex)
    items: list[ThreadItem] = Field(default_factory=list)
    usage_total: Usage = Field(default_factory=Usage)
    # Set only for subagent threads:
    agent_id: Optional[str] = None
    agent_type: Optional[str] = None
    description: Optional[str] = None
    parent_tool_use_id: Optional[str] = None


class ModelStat(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_tokens: int = 0
    messages: int = 0
    cost_usd: float = 0.0


class Totals(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_tokens: int = 0
    messages: int = 0
    sessions: int = 0
    cost_usd: float = 0.0


class Bucket(BaseModel):
    offset_seconds: int  # start offset from the window anchor
    cost_usd: float = 0.0
    total_tokens: int = 0


class WindowStat(BaseModel):
    label: str
    window_seconds: int
    anchor: Optional[datetime] = None  # first activity within the window
    elapsed_seconds: int = 0
    remaining_seconds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    total_tokens: int = 0
    messages: int = 0
    cost_usd: float = 0.0
    bucket_seconds: int = 0
    buckets: list[Bucket] = Field(default_factory=list)
    budget_usd: Optional[float] = None


class DailyStat(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    total_tokens: int = 0
    cost_usd: float = 0.0


class CostBreakdown(BaseModel):
    input: float = 0.0
    output: float = 0.0
    cache_write: float = 0.0
    cache_read: float = 0.0


class ToolCount(BaseModel):
    name: str
    count: int


class TopSession(BaseModel):
    session_id: str
    title: str
    cost_usd: float = 0.0
    total_tokens: int = 0
    messages: int = 0


class ProjectStat(BaseModel):
    project: str
    sessions: int = 0
    messages: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class HourStat(BaseModel):
    hour: int
    messages: int = 0
    cost_usd: float = 0.0


class Plan(BaseModel):
    label: str
    organization_name: Optional[str] = None
    organization_type: Optional[str] = None
    seat_tier: Optional[str] = None
    rate_limit_tier: Optional[str] = None
    has_extra_usage: bool = False
    extra_usage_disabled_reason: Optional[str] = None
    budget_source: Literal["estimated", "configured", "none"] = "none"
    five_hour_budget_usd: Optional[float] = None
    weekly_budget_usd: Optional[float] = None


class ClaudeModelUsage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class ClaudeDaily(BaseModel):
    date: str
    total_tokens: int = 0
    messages: int = 0
    tool_calls: int = 0
    sessions: int = 0


class ClaudeCacheStats(BaseModel):
    """Claude Code's own rolled-up usage (~/.claude/stats-cache.json)."""

    last_computed_date: Optional[str] = None
    total_sessions: int = 0
    total_messages: int = 0
    total_tool_calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    by_model: list[ClaudeModelUsage] = Field(default_factory=list)
    daily: list[ClaudeDaily] = Field(default_factory=list)


class SubagentTypePct(BaseModel):
    agent_type: str
    pct: float


class ContributingFactor(BaseModel):
    key: str
    pct: float
    label: str
    advice: str


class UsageInsights(BaseModel):
    window_hours: int
    total_tokens: int
    factors: list[ContributingFactor] = Field(default_factory=list)
    by_subagent_type: list[SubagentTypePct] = Field(default_factory=list)


class StatsResponse(BaseModel):
    generated_at: datetime
    plan: Optional[Plan] = None
    claude_cache: Optional[ClaudeCacheStats] = None
    insights: Optional[UsageInsights] = None
    totals: Totals
    by_model: list[ModelStat] = Field(default_factory=list)
    hours: WindowStat
    week: WindowStat
    daily: list[DailyStat] = Field(default_factory=list)
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)
    cache_hit_rate: float = 0.0
    cache_savings_usd: float = 0.0
    tools: list[ToolCount] = Field(default_factory=list)
    top_sessions: list[TopSession] = Field(default_factory=list)
    by_project: list[ProjectStat] = Field(default_factory=list)
    by_hour: list[HourStat] = Field(default_factory=list)


class SearchHit(BaseModel):
    """One cross-session full-text search match, linking to a message uuid."""

    session_id: str
    project_cwd: Optional[str] = None
    title: str = ""
    uuid: Optional[str] = None
    role: Optional[str] = None
    timestamp: Optional[datetime] = None
    snippet: str = ""  # contains \x02/\x03 markers around matched terms


class SearchResponse(BaseModel):
    query: str
    indexed_sessions: int = 0
    available: bool = True  # False if SQLite lacks FTS5
    hits: list[SearchHit] = Field(default_factory=list)


class NotifyConfig(BaseModel):
    """Phone/desktop push config. Delivery is outbound-only (no inbound server),
    so muse can notify from localhost. Currently targets ntfy (ntfy.sh or self-
    hosted): muse POSTs to {server}/{topic} and the ntfy app receives the push."""

    enabled: bool = False
    provider: str = "ntfy"
    server: str = "https://ntfy.sh"
    topic: str = ""
    priority: int = 3  # ntfy 1 (min) .. 5 (max)
    token: Optional[str] = None  # optional auth for protected/self-hosted topics


class NotifyResult(BaseModel):
    ok: bool
    detail: str = ""


class AlertRules(BaseModel):
    """Which session events should trigger a push notification."""

    on_waiting: bool = True  # a session finished a turn and is awaiting your input
    on_stopped: bool = False  # a session went idle/stopped
    on_error: bool = True  # a session hit an error (tool error / api error / system error)
    poll_seconds: int = 15


class AlertEvent(BaseModel):
    ts: datetime
    session_id: str
    title: str = ""
    kind: str  # waiting | stopped | error
    message: str = ""
    delivered: bool = False
    detail: str = ""  # delivery detail (HTTP status / error)


class Bookmark(BaseModel):
    message_uuid: str
    note: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Annotations(BaseModel):
    session_id: str
    custom_title: Optional[str] = None
    bookmarks: list[Bookmark] = Field(default_factory=list)


# --- Investigations: AI/user-authored markup documents that reference sessions --
# An Investigation is muse-owned (lives in ~/.muse/muse.db, never ~/.claude). It
# holds prose plus references that point into real sessions/messages; references
# are bidirectional — clickable into the session, and surfaced as backlinks on the
# session. The user's own Claude Code creates these over MCP and can read them back.

InvestigationAuthor = Literal["ai", "user"]


class InvestigationRef(BaseModel):
    """A pointer from an Investigation into a specific session (and optionally a
    specific message/tool step, via anchor_uuid → the viewer's ?focus= param)."""

    id: str
    session_id: str
    anchor_uuid: Optional[str] = None  # ThreadItem.uuid or ToolUse.id; deep-links via ?focus=
    label: str = ""  # short human label for the reference
    comment: str = ""  # why this step matters
    created_at: Optional[str] = None


class Investigation(BaseModel):
    id: str
    title: str
    body: str = ""  # markdown prose
    author: InvestigationAuthor = "ai"
    status: str = "open"  # free-form (e.g. open | resolved)
    refs: list[InvestigationRef] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class InvestigationSummary(BaseModel):
    id: str
    title: str
    author: InvestigationAuthor = "ai"
    status: str = "open"
    ref_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SessionBacklink(BaseModel):
    """One investigation reference, seen from the referenced session's side."""

    investigation_id: str
    investigation_title: str
    author: InvestigationAuthor = "ai"
    ref: InvestigationRef


# --- Worklog notes: lightweight running notes about active work ---------------
# Much lighter than an Investigation: one timestamped line of prose, optionally
# attached to a session/step, grouped by local day for the journal view.

NoteKind = Literal["note", "next", "brief"]


class Note(BaseModel):
    id: str
    session_id: Optional[str] = None  # None = global journal note
    anchor_uuid: Optional[str] = None  # optional step anchor; deep-links via ?focus=
    kind: NoteKind = "note"  # 'next' = open loop; 'brief' = AI re-entry summary
    author: InvestigationAuthor = "user"
    body: str
    day: str  # local YYYY-MM-DD, for journal grouping
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class LiveSession(BaseModel):
    session_id: str
    pid: int
    cwd: Optional[str] = None
    status: str = "unknown"  # busy | idle | waiting | shell | ...
    waiting_for: Optional[str] = None
    pane_id: Optional[str] = None  # tmux pane, e.g. "%42"
    version: Optional[str] = None
    updated_at: Optional[datetime] = None


ContextAction = Literal["none", "compact", "clear", "message", "stop"]
IdleMode = Literal["message", "suggestion"]


class AutopilotConfig(BaseModel):
    session_id: str
    enabled: bool = False
    idle_mode: IdleMode = "message"  # send `message`, or accept Claude's suggestion
    message: str = ""  # sent when idle and context is below threshold
    max_sends: int = 5
    sent_count: int = 0
    interval_seconds: int = 30
    last_sent_at: Optional[datetime] = None
    # Compaction / running-out-of-context policy:
    context_threshold_pct: int = 80
    context_action: ContextAction = "compact"
    context_message: str = ""  # used when context_action == "message"
    # Usage-limit back-off: when a usage/rate limit is detected, pause this long.
    backoff_seconds: int = 900
    backoff_until: Optional[datetime] = None  # runtime; set when backing off


class AutopilotLogEntry(BaseModel):
    ts: datetime
    session_id: str
    action: str  # injected | skipped | error | armed | disarmed | manual
    detail: str = ""


class AutopilotSession(BaseModel):
    session_id: str
    title: Optional[str] = None
    live: Optional[LiveSession] = None
    config: AutopilotConfig


class AutopilotState(BaseModel):
    armed: bool = False
    tmux_available: bool = True
    schedule_enabled: bool = False
    schedule_start_hour: int = 22
    schedule_end_hour: int = 7
    within_hours: bool = True  # whether the current local time is inside the window
    sessions: list[AutopilotSession] = Field(default_factory=list)
    recent_log: list[AutopilotLogEntry] = Field(default_factory=list)


class SessionSummary(BaseModel):
    session_id: str
    provider: str = "claude"  # claude | codex | …
    project_cwd: Optional[str] = None
    project_dir: str
    title: str
    title_source: TitleSource = "none"
    message_count: int = 0
    total_tokens: int = 0  # best-effort per-session token usage (0 if unknown)
    model: Optional[str] = None
    git_branch: Optional[str] = None
    mtime: datetime
    size_bytes: int = 0
    subagent_count: int = 0
    is_running: bool = False
    awaiting_user: bool = False
    state: Literal["live", "waiting", "stopped"] = "stopped"


class SubagentUsage(BaseModel):
    """Token + cost usage for one subagent run within a session."""

    agent_id: str
    agent_type: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0  # real work (in+out+cache-creation)
    cost_usd: float = 0.0  # authoritative (muse pricing), incl. cache reads
    spawn_anchor_uuid: Optional[str] = None  # parent tool_use that launched it (?focus=)


class TokenUsage(BaseModel):
    """Per-session token usage + authoritative cost. For Claude, subagent usage is
    rolled into the parent session (broken out via main_/subagent_ fields and the
    `subagents` list). `total_tokens` is real work (input + output + cache-creation,
    excluding cache reads) — matching the session list; `total_with_cache_read`
    includes the cached re-reads. `cost_usd` is muse-computed (pricing.py) and
    includes cache reads at their discounted rate — so it's the real dollar figure."""

    session_id: str
    provider: str = "claude"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    total_with_cache_read: int = 0
    main_tokens: int = 0  # real-work tokens from the main thread
    subagent_tokens: int = 0  # real-work tokens from subagents (Claude)
    subagent_count: int = 0
    cost_usd: float = 0.0  # authoritative full cost (0 if pricing unknown)
    main_cost_usd: float = 0.0
    subagent_cost_usd: float = 0.0
    models: list[str] = Field(default_factory=list)
    subagents: list[SubagentUsage] = Field(default_factory=list)
    breakdown_available: bool = True  # False when only a flat total is known


class UsagePoint(BaseModel):
    """Cumulative usage at one point in a session's timeline (a user turn)."""

    anchor_uuid: Optional[str] = None
    timestamp: Optional[datetime] = None
    label: str = ""  # the user prompt (first line)
    cumulative_tokens: int = 0  # real work up to here
    cumulative_cost_usd: float = 0.0  # authoritative, incl. cache reads


class UsageTimeline(BaseModel):
    session_id: str
    points: list[UsagePoint] = Field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    truncated: bool = False  # True if points were sampled to a cap


class UsageAtAnchor(BaseModel):
    """Cumulative spend up to (and including) a given step — for 'cost to reach X'."""

    session_id: str
    anchor_uuid: str
    found: bool = False
    cutoff_timestamp: Optional[datetime] = None
    cumulative_tokens: int = 0  # real work
    cumulative_cost_usd: float = 0.0  # authoritative, incl. cache reads
    event_count: int = 0  # usage events counted (main + subagents, merged by time)
