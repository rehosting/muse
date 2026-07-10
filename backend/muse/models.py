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
    # Windowed loads: total_items is the count in the FULL thread, window_start the
    # index of items[0] within it. None => the response is the complete thread.
    total_items: Optional[int] = None
    window_start: Optional[int] = None
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
    anchor: Optional[datetime] = None  # window start (observed reset or first activity)
    anchor_source: Literal["reset", "estimated"] = "estimated"
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


class Pack(BaseModel):
    """A context pack: hand-off markdown written to ~/.muse/packs/<id>.md that a
    newly launched session reads (the seed prompt names the absolute path)."""

    id: str
    title: str
    source_session_id: Optional[str] = None
    body_md: str
    path: str
    created_at: Optional[str] = None


class BoardActivity(BaseModel):
    """The last meaningful thing a session did (extracted incrementally from
    appended transcript bytes — never a full parse)."""

    kind: str = ""  # assistant_text | tool_call | user | error
    text: str = ""
    tool: Optional[str] = None
    ts: Optional[datetime] = None


class BoardCard(BaseModel):
    """One session on the mission-control board."""

    session_id: str
    provider: str = "claude"
    title: str = ""
    project_cwd: Optional[str] = None
    state: Literal["live", "waiting", "stopped"] = "stopped"
    live_status: Optional[str] = None  # busy | idle | waiting (from the live pid map)
    waiting_for: Optional[str] = None
    has_pane: bool = False
    pane_id: Optional[str] = None
    context_pct: Optional[float] = None
    total_tokens: int = 0  # real work tokens (excl. cache reads)
    cost_usd: float = 0.0
    health: Optional[Literal["ok", "warn", "bad"]] = None
    health_flags: list[str] = Field(default_factory=list)
    last_activity: Optional[BoardActivity] = None
    # No idle_seconds field on purpose: it would change every tick and make
    # every card "updated" in the SSE diff — the client derives idle from mtime.
    mtime: datetime
    model: Optional[str] = None
    git_branch: Optional[str] = None
    agent_kind: Optional[str] = None


class BoardSnapshot(BaseModel):
    generated_at: datetime
    cards: list[BoardCard] = Field(default_factory=list)


class SessionOutcome(BaseModel):
    """What one session cost vs. what it produced (evidence-based provenance)."""

    session_id: str
    title: str = ""
    project_cwd: Optional[str] = None
    provider: str = "claude"
    model: Optional[str] = None
    cost_usd: float = 0.0
    work_tokens: int = 0
    started: Optional[datetime] = None
    ended: Optional[datetime] = None
    duration_seconds: float = 0.0
    commits_high: int = 0
    commits_medium: int = 0
    commits_low: int = 0
    commit_subjects: list[str] = Field(default_factory=list)  # up to 3, high+medium
    health: Optional[str] = None  # ok | warn | bad
    error_count: int = 0


class OutcomeRatio(BaseModel):
    """Aggregate productivity for a key (a project or a model)."""

    key: str
    cost_usd: float = 0.0
    commits: int = 0  # high+medium only
    commits_low: int = 0
    sessions: int = 0
    commits_per_10usd: Optional[float] = None  # null when cost < $1 (noise)


class HeatDay(BaseModel):
    day: str  # local YYYY-MM-DD
    cost_usd: float = 0.0
    work_tokens: int = 0


class MatrixCell(BaseModel):
    dow: int  # 0=Mon … 6=Sun
    hour: int  # 0–23 local
    activity: int = 0  # assistant messages
    cost_usd: float = 0.0
    errors: int = 0
    commits: int = 0


class OutcomesResponse(BaseModel):
    generated_at: datetime
    range_days: int
    confidence_policy: str = "high+medium"
    outcomes: list[SessionOutcome] = Field(default_factory=list)
    most_productive: list[SessionOutcome] = Field(default_factory=list)
    most_wasteful: list[SessionOutcome] = Field(default_factory=list)
    by_project: list[OutcomeRatio] = Field(default_factory=list)
    by_model: list[OutcomeRatio] = Field(default_factory=list)
    calendar: list[HeatDay] = Field(default_factory=list)
    matrix: list[MatrixCell] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TimelineCommit(BaseModel):
    commit_hash: str
    subject: str = ""
    ts: Optional[datetime] = None
    confidence: Optional[str] = None  # high | medium | low | None (unmatched)


class TimelineSession(BaseModel):
    session_id: str
    title: str = ""
    started: Optional[datetime] = None
    ended: Optional[datetime] = None
    cost_usd: float = 0.0
    health: Optional[str] = None
    commits: list[TimelineCommit] = Field(default_factory=list)


class TimelineResponse(BaseModel):
    project: str
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    sessions: list[TimelineSession] = Field(default_factory=list)
    unmatched_commits: list[TimelineCommit] = Field(default_factory=list)


class AIJob(BaseModel):
    """A queued/running/finished headless `claude -p` job (ask, summary, digest,
    retro). Stored in ~/.muse/muse.db; executed one-at-a-time by the AI worker."""

    id: str
    kind: str  # ask | session_summary | daily_digest | weekly_retro
    params: dict = Field(default_factory=dict)
    status: str = "queued"  # queued | running | done | error | cancelled
    result: Optional[dict] = None  # {answer_md, output_ref?: {type, id}}
    error: Optional[str] = None
    model: Optional[str] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class AIStatus(BaseModel):
    """Health of the AI layer: is the claude CLI reachable, what's queued."""

    available: bool = False
    model: str = ""
    queued: int = 0
    running: int = 0
    total_cost_usd: float = 0.0
    last_error: Optional[str] = None


class AgentTypeStat(BaseModel):
    """Spend split by subagent type ('main thread' = the top-level session)."""

    agent_type: str
    cost_usd: float = 0.0
    total_tokens: int = 0
    messages: int = 0


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
    range_days: int = 0  # reporting range (0 = all time); windows ignore this
    plan: Optional[Plan] = None
    claude_cache: Optional[ClaudeCacheStats] = None
    insights: Optional[UsageInsights] = None
    totals: Totals
    by_model: list[ModelStat] = Field(default_factory=list)
    by_agent_type: list[AgentTypeStat] = Field(default_factory=list)
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
    loose: bool = False  # AND query found nothing; these hits are the OR fallback
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
    # Web Push (VAPID) is an additional, independent channel: a device subscribes
    # once while connected, then the browser's push service delivers over the public
    # internet even when the phone is off the tailnet. Outbound-only, like ntfy.
    web_push_enabled: bool = False


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


class PushSubscription(BaseModel):
    """A browser Web Push subscription, stored per device so muse can deliver to it."""

    endpoint: str
    keys: dict[str, str] = Field(default_factory=dict)  # {p256dh, auth}
    label: str = ""  # human-friendly device name
    created_at: Optional[str] = None


class PendingOption(BaseModel):
    id: str  # "1".."9" for menus; option index for tool questions; "other" for free-text
    label: str
    description: Optional[str] = None
    kind: Literal["menu", "free_text"] = "menu"


class PendingOptions(BaseModel):
    """What a live session is currently asking the user to choose between."""

    session_id: str
    source: Literal["permission", "tool_question", "none"] = "none"
    available: bool = False  # False when nothing is pending / actionable
    prompt: str = ""
    detail: str = ""  # long-form context to review before answering (e.g. a plan body)
    options: list[PendingOption] = Field(default_factory=list)
    current_index: Optional[int] = None  # highlighted row in the live buffer
    fingerprint: str = ""  # client echoes this back on select for stale-protection
    remaining_questions: int = 0  # AskUserQuestion with >1 question still pending
    pane_id: Optional[str] = None
    in_tmux: bool = True  # False => process found but not running inside tmux
    reason: Optional[str] = None  # why nothing actionable (for UI messaging)


class SuggestReply(BaseModel):
    text: str


class TmuxPane(BaseModel):
    """One pane in the live tmux topology, for the swipeable mobile panes view."""

    provider: Optional[str] = None  # claude | gemini | codex | opencode | None
    pane_id: str
    session_name: str
    window_index: int
    window_id: str = ""  # stable window handle (@<n>) — the target for move-window
    window_name: str
    window_active: bool
    pane_index: int
    pane_active: bool
    command: str
    cwd: str
    title: str = ""
    session_attached: bool = True  # False => detached session (scratch/background)
    last_activity: int = 0  # epoch secs of last window activity (most-recent sort)
    muse_session_id: Optional[str] = None  # set if this pane runs a tracked session
    context_pct: Optional[float] = None  # context-window occupancy (tracked sessions)
    queued: int = 0  # replies queued for delivery when this session's turn ends
    # Attention status for grouping the mobile task list.
    # responded = a live session whose turn ended (a response is ready for you).
    status: Literal["needs_you", "responded", "working", "idle"] = "idle"
    attention: str = ""  # short reason, e.g. "permission prompt", "working"
    # Claude Code's permission mode (Shift+Tab cycles it); None for non-Claude panes.
    mode: Optional[Literal["default", "acceptEdits", "plan", "bypass"]] = None
    capabilities: dict[str, bool] = Field(default_factory=dict)
    # Full screen text is heavy (hundreds of KB across a fleet) — it ships only
    # when the client asks (?previews=1); the one-line tail always ships for
    # task-list subtitles. The deck fetches live screens per-pane instead.
    preview: str = ""  # visible screen (ANSI), only when previews=1
    preview_tail: str = ""  # last visible line (ANSI stripped, short)
    options: list[PendingOption] = Field(default_factory=list)  # menu detected in buffer


class TmuxLayout(BaseModel):
    available: bool = True  # False when tmux isn't installed/running
    panes: list[TmuxPane] = Field(default_factory=list)
    reason: Optional[str] = None


class SlashCommand(BaseModel):
    """One entry in the composer's "/" autocomplete: a Claude Code slash command
    available to the pane's session (built-in, user-global, or project-local)."""

    name: str  # invoked as "/{name}" — subdir commands are namespaced with ":"
    description: str = ""
    source: Literal["builtin", "user", "project"] = "builtin"


class QueuedReply(BaseModel):
    """A user-authored message waiting to be typed into a session's pane the next
    time that session is genuinely idle (turn ended, no menu pending)."""

    id: int
    session_id: str
    text: str
    created_at: Optional[datetime] = None
    status: Literal["pending", "sent", "cancelled", "failed"] = "pending"
    # turn = deliver alone and let it run a full turn; append = glue onto the
    # previous queued item so both go in one message.
    mode: Literal["turn", "append"] = "turn"
    sent_at: Optional[datetime] = None
    error: Optional[str] = None


class QueueView(BaseModel):
    """A session's queue plus why it isn't delivering right now (so the UI can
    explain a held reply instead of leaving it silently pending)."""

    items: list[QueuedReply] = []
    hold_reason: Optional[str] = None


class RunwaySession(BaseModel):
    session_id: str
    title: str = ""
    cost_usd: float = 0.0


class RunwayWindow(BaseModel):
    """Spend vs budget for one rate-limit window (5h or weekly)."""

    label: str
    window_seconds: int
    anchor: Optional[datetime] = None  # window start
    anchor_source: str = "estimated"  # "reset" when anchored to an observed reset
    elapsed_seconds: int = 0
    remaining_seconds: int = 0
    cost_usd: float = 0.0
    budget_usd: Optional[float] = None
    # Where the budget came from: "configured" (MUSE_LIMIT_*_USD), "observed"
    # (spend at the last real limit hit), or "none" (subscription plan with no
    # calibration yet — show spend without a ceiling, never a made-up estimate).
    budget_source: str = "none"
    pct_used: Optional[float] = None  # cost/budget, None without a budget
    pct_elapsed: float = 0.0  # time progress through the window


class RunwayResponse(BaseModel):
    """How much headroom is left before hitting the plan's usage limits — the
    fleet-driving question ('can I keep all these sessions running?')."""

    generated_at: datetime
    plan_label: Optional[str] = None
    five_hour: RunwayWindow
    week: RunwayWindow
    burn_usd_per_hour: float = 0.0  # trailing burn rate (last 30 min, annualized to 1h)
    projected_exhaust_at: Optional[datetime] = None  # when the 5h budget runs out at this burn
    exhaust_before_reset: bool = False  # True => you'll hit the limit before the window resets
    top_sessions: list[RunwaySession] = Field(default_factory=list)  # burners this 5h window


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

# A retro(spective) is structurally an investigation — markdown + session refs —
# tagged so the UI can filter and badge it separately.
InvestigationKind = Literal["investigation", "retro"]


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
    kind: InvestigationKind = "investigation"
    refs: list[InvestigationRef] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class InvestigationSummary(BaseModel):
    id: str
    title: str
    author: InvestigationAuthor = "ai"
    status: str = "open"
    kind: InvestigationKind = "investigation"
    ref_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SessionBacklink(BaseModel):
    """One investigation reference, seen from the referenced session's side."""

    investigation_id: str
    investigation_title: str
    author: InvestigationAuthor = "ai"
    kind: InvestigationKind = "investigation"
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
IdleMode = Literal["message", "suggestion", "ai"]


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
    health: Optional[Literal["ok", "warn", "bad"]] = None  # failure-pattern badge


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
