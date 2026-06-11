// Mirror of backend/muse/models.py — keep in sync with the Pydantic contract.

export type Role = "user" | "assistant" | "system" | "other";
export type BlockKind = "text" | "thinking" | "tool_use";
export type TitleSource = "ai-title" | "user" | "slug" | "none";

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  service_tier: string | null;
}

export interface ToolResult {
  tool_use_id: string;
  content: string | null;
  is_error: boolean;
  truncated: boolean;
  cache_id: string | null;
  preview: string | null;
}

export interface SubagentRef {
  agent_id: string;
  agent_type: string;
  description: string;
  tool_use_id: string;
}

export interface ToolUse {
  id: string;
  name: string;
  input: Record<string, unknown>;
  caller: Record<string, unknown> | null;
  result: ToolResult | null;
  subagent: SubagentRef | null;
}

export interface ContentBlock {
  kind: BlockKind;
  text: string | null;
  tool_use: ToolUse | null;
}

export interface ThreadItem {
  uuid: string;
  parent_uuid: string | null;
  role: Role;
  type: string;
  timestamp: string | null;
  blocks: ContentBlock[];
  text: string | null;
  usage: Usage | null;
  model: string | null;
  is_sidechain: boolean;
  level: string | null;
}

export interface Thread {
  session_id: string;
  provider: string;
  project_cwd: string | null;
  version: string | null;
  title: string;
  title_source: TitleSource;
  model: string | null;
  context_window: number | null;
  items: ThreadItem[];
  usage_total: Usage;
  agent_id: string | null;
  agent_type: string | null;
  description: string | null;
  parent_tool_use_id: string | null;
}

export interface SessionSummary {
  session_id: string;
  provider: string;
  project_cwd: string | null;
  project_dir: string;
  title: string;
  title_source: TitleSource;
  message_count: number;
  total_tokens: number;
  model: string | null;
  git_branch: string | null;
  mtime: string;
  size_bytes: number;
  subagent_count: number;
  is_running: boolean;
  awaiting_user: boolean;
  state: "live" | "waiting" | "stopped";
  health: "ok" | "warn" | "bad" | null;
}

export interface SessionHealth {
  score: "ok" | "warn" | "bad";
  error_count: number;
  retry_loops: { tool: string | null; label: string; times: number; anchors: (string | null)[] }[];
  error_spirals: { start_anchor: string | null; errors: number; window: number }[];
  permission_denials: { label: string; anchor: string | null }[];
}

export interface ModelStat {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
  messages: number;
  cost_usd: number;
}

export interface Totals {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
  messages: number;
  sessions: number;
  cost_usd: number;
}

export interface Bucket {
  offset_seconds: number;
  cost_usd: number;
  total_tokens: number;
}

export interface WindowStat {
  label: string;
  window_seconds: number;
  anchor: string | null;
  anchor_source: "reset" | "estimated";
  elapsed_seconds: number;
  remaining_seconds: number;
  input_tokens: number;
  output_tokens: number;
  cache_tokens: number;
  total_tokens: number;
  messages: number;
  cost_usd: number;
  bucket_seconds: number;
  buckets: Bucket[];
  budget_usd: number | null;
}

export interface DailyStat {
  date: string;
  total_tokens: number;
  cost_usd: number;
}

export interface CostBreakdown {
  input: number;
  output: number;
  cache_write: number;
  cache_read: number;
}

export interface ToolCount {
  name: string;
  count: number;
}

export interface TopSession {
  session_id: string;
  title: string;
  cost_usd: number;
  total_tokens: number;
  messages: number;
}

export interface ProjectStat {
  project: string;
  sessions: number;
  messages: number;
  total_tokens: number;
  cost_usd: number;
}

export interface HourStat {
  hour: number;
  messages: number;
  cost_usd: number;
}

export interface Plan {
  label: string;
  organization_name: string | null;
  organization_type: string | null;
  seat_tier: string | null;
  rate_limit_tier: string | null;
  has_extra_usage: boolean;
  extra_usage_disabled_reason: string | null;
  budget_source: "estimated" | "configured" | "none";
  five_hour_budget_usd: number | null;
  weekly_budget_usd: number | null;
}

export interface ClaudeModelUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface ClaudeDaily {
  date: string;
  total_tokens: number;
  messages: number;
  tool_calls: number;
  sessions: number;
}

export interface ClaudeCacheStats {
  last_computed_date: string | null;
  total_sessions: number;
  total_messages: number;
  total_tool_calls: number;
  total_tokens: number;
  cost_usd: number;
  by_model: ClaudeModelUsage[];
  daily: ClaudeDaily[];
}

export interface SubagentTypePct {
  agent_type: string;
  pct: number;
}

export interface ContributingFactor {
  key: string;
  pct: number;
  label: string;
  advice: string;
}

export interface UsageInsights {
  window_hours: number;
  total_tokens: number;
  factors: ContributingFactor[];
  by_subagent_type: SubagentTypePct[];
}

export interface AgentTypeStat {
  agent_type: string;
  cost_usd: number;
  total_tokens: number;
  messages: number;
}

export interface StatsResponse {
  generated_at: string;
  range_days: number;
  plan: Plan | null;
  claude_cache: ClaudeCacheStats | null;
  insights: UsageInsights | null;
  totals: Totals;
  by_model: ModelStat[];
  by_agent_type: AgentTypeStat[];
  hours: WindowStat;
  week: WindowStat;
  daily: DailyStat[];
  cost_breakdown: CostBreakdown;
  cache_hit_rate: number;
  cache_savings_usd: number;
  tools: ToolCount[];
  top_sessions: TopSession[];
  by_project: ProjectStat[];
  by_hour: HourStat[];
}

export interface Bookmark {
  message_uuid: string;
  note: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface Annotations {
  session_id: string;
  custom_title: string | null;
  bookmarks: Bookmark[];
}

export type InvestigationAuthor = "ai" | "user";

export interface InvestigationRef {
  id: string;
  session_id: string;
  anchor_uuid: string | null;
  label: string;
  comment: string;
  created_at: string | null;
}

export type InvestigationKind = "investigation" | "retro";

export interface Investigation {
  id: string;
  title: string;
  body: string;
  author: InvestigationAuthor;
  status: string;
  kind: InvestigationKind;
  refs: InvestigationRef[];
  created_at: string | null;
  updated_at: string | null;
}

export interface InvestigationSummary {
  id: string;
  title: string;
  author: InvestigationAuthor;
  status: string;
  kind: InvestigationKind;
  ref_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface SessionBacklink {
  investigation_id: string;
  investigation_title: string;
  author: InvestigationAuthor;
  kind: InvestigationKind;
  ref: InvestigationRef;
}

export type NoteKind = "note" | "next" | "brief";

export interface Note {
  id: string;
  session_id: string | null;
  anchor_uuid: string | null;
  kind: NoteKind;
  author: InvestigationAuthor;
  body: string;
  day: string; // local YYYY-MM-DD
  created_at: string | null;
  updated_at: string | null;
}

export interface ReentryBrief {
  session_id: string;
  title: string | null;
  provider: string | null;
  project_cwd: string | null;
  state: "live" | "waiting" | "stopped" | null;
  mtime: string | null;
  idle_seconds: number | null;
  last_goal: { text: string; anchor_uuid: string | null } | null;
  last_assistant: { text: string; anchor_uuid: string | null } | null;
  open_todos: { content: string; status: string }[];
  done_todos: number;
  open_errors: { label: string; detail: string; anchor_uuid: string | null }[];
  files: { path: string; reads: number; edits: number; writes: number; last_ts: string | null }[];
  next_notes: { id: string; body: string; created_at: string | null }[];
  latest_ai_brief: { id: string; body: string; created_at: string | null } | null;
  note_count: number;
  reference_freshness: Record<string, unknown> | null;
  resume_command: string | null;
}

export interface Journal {
  day: string;
  notes: Note[];
  sessions: SessionSummary[];
}

export interface FileHit {
  file_path: string;
  basename: string;
  session_count: number;
  reads: number | null;
  edits: number | null;
  writes: number | null;
  errors: number | null;
  last_ts: string | null;
}

export interface FileActivityOp {
  op: "read" | "edit" | "write";
  tool_use_id: string | null;
  is_error: boolean;
  ts: string | null;
}

export interface FileActivityGroup {
  session_id: string;
  provider: string;
  project_cwd: string | null;
  title?: string | null;
  ops: FileActivityOp[];
  reads: number;
  edits: number;
  writes: number;
  errors: number;
  first_ts: string | null;
  last_ts: string | null;
}

export interface RelatedSession {
  summary: SessionSummary;
  score: number;
  shared_files: string[];
  same_branch: boolean;
}

export interface OpenLoop {
  summary: SessionSummary;
  last_user_label: string | null;
  open_todo_count: number;
  next_notes: { id: string; body: string; created_at: string | null }[];
  open_error_count: number;
}

export interface LiveSession {
  session_id: string;
  pid: number;
  cwd: string | null;
  status: string;
  waiting_for: string | null;
  pane_id: string | null;
  version: string | null;
  updated_at: string | null;
}

export type ContextAction = "none" | "compact" | "clear" | "message" | "stop";
export type IdleMode = "message" | "suggestion";

export interface AutopilotConfig {
  session_id: string;
  enabled: boolean;
  idle_mode: IdleMode;
  message: string;
  max_sends: number;
  sent_count: number;
  interval_seconds: number;
  last_sent_at: string | null;
  context_threshold_pct: number;
  context_action: ContextAction;
  context_message: string;
  backoff_seconds: number;
  backoff_until: string | null;
}

export interface AutopilotPolicy {
  session_ids: string[];
  enabled: boolean;
  idle_mode: IdleMode;
  message: string;
  max_sends: number;
  interval_seconds: number;
  context_threshold_pct: number;
  context_action: ContextAction;
  context_message: string;
  backoff_seconds: number;
}

export interface AutopilotLogEntry {
  ts: string;
  session_id: string;
  action: string;
  detail: string;
}

export interface AutopilotSession {
  session_id: string;
  title: string | null;
  live: LiveSession | null;
  config: AutopilotConfig;
}

export interface AutopilotState {
  armed: boolean;
  tmux_available: boolean;
  schedule_enabled: boolean;
  schedule_start_hour: number;
  schedule_end_hour: number;
  within_hours: boolean;
  sessions: AutopilotSession[];
  recent_log: AutopilotLogEntry[];
}

export type EventKind =
  | "user"
  | "assistant_text"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "subagent"
  | "system"
  | "lifecycle";

export interface SessionEvent {
  index: number;
  kind: EventKind;
  type: string;
  role: string | null;
  timestamp: string | null;
  label: string;
  detail: string | null;
  anchor_uuid: string | null;
  tool_use_id: string | null;
  tool_name: string | null;
  status: string | null;
  is_error: boolean;
  level: string | null;
  duration_ms: number | null;
  subagent: SubagentRef | null;
  is_compaction: boolean;
}

export interface CompactionBoundary {
  uuid: string | null;
  timestamp: string | null;
  trigger: string | null;
  pre_tokens: number | null;
  duration_ms: number | null;
}

export interface SessionLineage {
  session_id: string;
  segment_count: number;
  total_pre_tokens: number;
  boundaries: CompactionBoundary[];
}

export interface PersistedOutput {
  content: string;
  offset: number;
  size_bytes: number;
  truncated: boolean;
}

export interface NotifyConfig {
  enabled: boolean;
  provider: string;
  server: string;
  topic: string;
  priority: number;
  token: string | null;
}

export interface NotifyResult {
  ok: boolean;
  detail: string;
}

export interface AlertRules {
  on_waiting: boolean;
  on_stopped: boolean;
  on_error: boolean;
  poll_seconds: number;
}

export interface AlertEvent {
  ts: string;
  session_id: string;
  title: string;
  kind: string;
  message: string;
  delivered: boolean;
  detail: string;
}

export interface SearchHit {
  session_id: string;
  project_cwd: string | null;
  title: string;
  uuid: string | null;
  role: string | null;
  timestamp: string | null;
  snippet: string; // contains \x02/\x03 markers around matched terms
}

export interface SearchResponse {
  query: string;
  indexed_sessions: number;
  available: boolean;
  loose: boolean; // exact query found nothing; hits are the any-term fallback
  hits: SearchHit[];
}

export type FileOpKind = "read" | "edit" | "write";

export interface FileOp {
  tool_use_id: string;
  kind: FileOpKind;
  tool_name: string;
  timestamp: string | null;
  is_error: boolean;
  edit_count: number;
}

export interface FileChange {
  path: string;
  read_count: number;
  edit_count: number;
  write_count: number;
  error_count: number;
  first_ts: string | null;
  last_ts: string | null;
  ops: FileOp[];
}
