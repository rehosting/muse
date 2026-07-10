import type {
  AIJob,
  AIStatus,
  Annotations,
  BoardSnapshot,
  CommitSearchHit,
  OutcomesResponse,
  SessionCommit,
  TimelineResponse,
  AutopilotPolicy,
  AutopilotState,
  AlertEvent,
  AlertRules,
  Bookmark,
  FileActivityGroup,
  FileChange,
  FileHit,
  Investigation,
  InvestigationSummary,
  Journal,
  LaunchResult,
  Note,
  NoteKind,
  NotifyConfig,
  OpenLoop,
  NotifyResult,
  PaneScreen,
  SlashCommand,
  PendingOptions,
  PushSubscriptionInfo,
  QueuedReply,
  QueueView,
  RunwayResponse,
  HooksStatus,
  TmuxLayout,
  Pack,
  LayoutSnapshot,
  PersistedOutput,
  Profile,
  ReentryBrief,
  RelatedSession,
  SearchResponse,
  SessionBacklink,
  SessionEvent,
  SessionHealth,
  SessionLineage,
  SessionSummary,
  StatsResponse,
  Thread,
  ThreadWindowOpts,
} from "./types";

function notifyAuthRequired(status: number): void {
  // Remote access: a 401 means the auth cookie is missing/expired — LoginGate
  // listens for this and shows the token prompt.
  if (status === 401) window.dispatchEvent(new Event("muse:auth-required"));
}

// FastAPI reports errors as `{ "detail": "..." }`; prefer that human message over the
// bare status line so config/validation errors (e.g. a broken profiles.toml) surface.
async function errorMessage(res: Response, url: string): Promise<string> {
  try {
    const body = (await res.clone().json()) as { detail?: unknown };
    if (typeof body?.detail === "string" && body.detail) return body.detail;
  } catch {
    /* not JSON — fall through to the status line */
  }
  return `${res.status} ${res.statusText} for ${url}`;
}

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) {
    notifyAuthRequired(res.status);
    throw new Error(await errorMessage(res, url));
  }
  return res.json() as Promise<T>;
}

async function sendJSON<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    notifyAuthRequired(res.status);
    throw new Error(await errorMessage(res, url));
  }
  return res.json() as Promise<T>;
}

export const api = {
  listSessions: () => getJSON<SessionSummary[]>("/api/sessions"),

  getStats: (days = 0) => getJSON<StatsResponse>(`/api/stats?days=${days}`),

  search: (q: string, limit = 30, signal?: AbortSignal) =>
    getJSON<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`, signal),

  // No opts => full thread (back-compat). With a limit, the server ships only that
  // window (default anchor: head for finished sessions, tail for live ones).
  getThread: (sessionId: string, opts?: ThreadWindowOpts) => {
    const qs = new URLSearchParams();
    if (opts?.limit != null) qs.set("limit", String(opts.limit));
    if (opts?.anchor) qs.set("anchor", opts.anchor);
    if (opts?.before != null) qs.set("before", String(opts.before));
    if (opts?.after != null) qs.set("after", String(opts.after));
    if (opts?.around) qs.set("around", opts.around);
    const q = qs.toString();
    return getJSON<Thread>(`/api/sessions/${sessionId}${q ? `?${q}` : ""}`);
  },

  getSubagent: (sessionId: string, agentId: string) =>
    getJSON<Thread>(`/api/sessions/${sessionId}/subagents/${agentId}`),

  getEvents: (sessionId: string, agentId?: string) =>
    getJSON<SessionEvent[]>(
      agentId
        ? `/api/sessions/${sessionId}/subagents/${agentId}/events`
        : `/api/sessions/${sessionId}/events`,
    ),

  getFiles: (sessionId: string, agentId?: string) =>
    getJSON<FileChange[]>(
      agentId
        ? `/api/sessions/${sessionId}/subagents/${agentId}/files`
        : `/api/sessions/${sessionId}/files`,
    ),

  getLineage: (sessionId: string) =>
    getJSON<SessionLineage>(`/api/sessions/${sessionId}/lineage`),

  getToolResult: (sessionId: string, cacheId: string, offset = 0) =>
    getJSON<PersistedOutput>(
      `/api/sessions/${sessionId}/tool-results/${cacheId}?offset=${offset}`,
    ),

  streamUrl: (sessionId: string) => `/api/sessions/${sessionId}/stream`,

  getAnnotations: (sessionId: string) =>
    getJSON<Annotations>(`/api/sessions/${sessionId}/annotations`),

  setTitle: (sessionId: string, title: string | null) =>
    sendJSON<Annotations>("PUT", `/api/sessions/${sessionId}/title`, { title }),

  upsertBookmark: (sessionId: string, messageUuid: string, note: string) =>
    sendJSON<Bookmark>("PUT", `/api/sessions/${sessionId}/bookmarks/${messageUuid}`, { note }),

  deleteBookmark: (sessionId: string, messageUuid: string) =>
    sendJSON<{ ok: boolean }>("DELETE", `/api/sessions/${sessionId}/bookmarks/${messageUuid}`),

  getNotifyConfig: () => getJSON<NotifyConfig>("/api/notify"),

  setNotifyConfig: (cfg: NotifyConfig) =>
    sendJSON<NotifyConfig>("PUT", "/api/notify", cfg),

  testNotify: (cfg: NotifyConfig) =>
    sendJSON<NotifyResult>("POST", "/api/notify/test", cfg),

  getAlertRules: () => getJSON<AlertRules>("/api/notify/rules"),

  setAlertRules: (rules: AlertRules) =>
    sendJSON<AlertRules>("PUT", "/api/notify/rules", rules),

  getAlertLog: () => getJSON<AlertEvent[]>("/api/notify/log"),

  getAutopilot: () => getJSON<AutopilotState>("/api/autopilot"),

  armAutopilot: (armed: boolean) =>
    sendJSON<AutopilotState>("POST", "/api/autopilot/arm", { armed }),

  setAutopilotSchedule: (enabled: boolean, start_hour: number, end_hour: number) =>
    sendJSON<AutopilotState>("POST", "/api/autopilot/schedule", {
      enabled,
      start_hour,
      end_hour,
    }),

  applyAutopilotPolicy: (policy: AutopilotPolicy) =>
    sendJSON<AutopilotState>("POST", "/api/autopilot/policy", policy),

  autopilotSend: (sessionId: string) =>
    sendJSON<{ ok: boolean }>("POST", `/api/autopilot/sessions/${sessionId}/send`),

  // --- investigations (AI/user markup documents) ---
  listInvestigations: () => getJSON<InvestigationSummary[]>("/api/investigations"),

  getInvestigation: (id: string) =>
    getJSON<Investigation>(`/api/investigations/${id}`),

  createInvestigation: (body: { title: string; body?: string; status?: string }) =>
    sendJSON<Investigation>("POST", "/api/investigations", { author: "user", ...body }),

  updateInvestigation: (
    id: string,
    body: { title?: string; body?: string; status?: string },
  ) => sendJSON<Investigation>("PUT", `/api/investigations/${id}`, body),

  deleteInvestigation: (id: string) =>
    sendJSON<{ ok: boolean }>("DELETE", `/api/investigations/${id}`),

  removeReference: (investigationId: string, refId: string) =>
    sendJSON<{ ok: boolean }>(
      "DELETE",
      `/api/investigations/${investigationId}/refs/${refId}`,
    ),

  getSessionReferences: (sessionId: string) =>
    getJSON<SessionBacklink[]>(`/api/sessions/${sessionId}/references`),

  // --- worklog notes (lightweight running notes + journal) ---
  listNotes: (filter: { sessionId?: string; day?: string; kind?: string } = {}) => {
    const p = new URLSearchParams();
    if (filter.sessionId) p.set("session_id", filter.sessionId);
    if (filter.day) p.set("day", filter.day);
    if (filter.kind) p.set("kind", filter.kind);
    const qs = p.toString();
    return getJSON<Note[]>(`/api/notes${qs ? `?${qs}` : ""}`);
  },

  createNote: (body: {
    body: string;
    session_id?: string | null;
    anchor_uuid?: string | null;
    kind?: NoteKind;
  }) => sendJSON<Note>("POST", "/api/notes", body),

  updateNote: (id: string, body: { body?: string; kind?: NoteKind }) =>
    sendJSON<Note>("PUT", `/api/notes/${id}`, body),

  deleteNote: (id: string) => sendJSON<{ ok: boolean }>("DELETE", `/api/notes/${id}`),

  getJournal: (day: string) =>
    getJSON<Journal>(`/api/journal/${day}`),

  getReentryBrief: (sessionId: string) =>
    getJSON<ReentryBrief>(`/api/sessions/${sessionId}/brief`),

  getOpenLoops: () => getJSON<OpenLoop[]>("/api/open-loops"),

  // --- cross-session file activity ---
  searchFiles: (q: string, limit = 50) =>
    getJSON<FileHit[]>(`/api/files/search?q=${encodeURIComponent(q)}&limit=${limit}`),

  getFileActivity: (path: string) =>
    getJSON<FileActivityGroup[]>(`/api/files/activity?path=${encodeURIComponent(path)}`),

  getRelatedSessions: (sessionId: string) =>
    getJSON<RelatedSession[]>(`/api/sessions/${sessionId}/related`),

  getSessionHealth: (sessionId: string) =>
    getJSON<SessionHealth>(`/api/sessions/${sessionId}/health`),

  // --- launcher + context packs ---
  createPack: (body: {
    source_session_id?: string | null;
    include_brief?: boolean;
    note_ids?: string[];
    include_files?: boolean;
    extra_md?: string;
    title?: string;
  }) => sendJSON<Pack>("POST", "/api/packs", body),

  launchSession: (body: { cwd: string; prompt?: string; pack_id?: string | null }) =>
    sendJSON<LaunchResult>("POST", "/api/launch", body),

  getLaunchTargets: () => getJSON<string[]>("/api/launch/targets"),

  // --- AI layer (headless claude -p jobs; enqueue then poll the job) ---
  askMuse: (question: string) => sendJSON<AIJob>("POST", "/api/ai/ask", { question }),

  getAiJob: (id: string) => getJSON<AIJob>(`/api/ai/jobs/${id}`),

  listAiJobs: (limit = 50, kind?: string) =>
    getJSON<AIJob[]>(`/api/ai/jobs?limit=${limit}${kind ? `&kind=${kind}` : ""}`),

  cancelAiJob: (id: string) =>
    sendJSON<{ ok: boolean }>("POST", `/api/ai/jobs/${id}/cancel`),

  summarizeSession: (sessionId: string) =>
    sendJSON<AIJob>("POST", `/api/sessions/${sessionId}/summarize`),

  generateDailyDigest: (day?: string) =>
    sendJSON<AIJob>("POST", "/api/ai/digest/daily", { day: day ?? "" }),

  generateWeeklyRetro: (weekStart?: string) =>
    sendJSON<AIJob>("POST", "/api/ai/retro/weekly", { week_start: weekStart ?? "" }),

  getAiStatus: () => getJSON<AIStatus>("/api/ai/status"),

  draftReply: (sessionId: string) =>
    sendJSON<AIJob>("POST", `/api/sessions/${sessionId}/draft-reply`),

  diagnoseSession: (sessionId: string) =>
    sendJSON<AIJob>("POST", `/api/sessions/${sessionId}/diagnose`),

  triageBoard: (sessionIds: string[]) =>
    sendJSON<AIJob>("POST", "/api/board/triage", { session_ids: sessionIds }),

  // --- insights (outcome-aware analytics) ---
  getInsights: (days = 30) =>
    getJSON<OutcomesResponse>(`/api/insights?days=${days}`),

  getInsightsTimeline: (project: string, days = 30) =>
    getJSON<TimelineResponse>(
      `/api/insights/timeline?project=${encodeURIComponent(project)}&days=${days}`,
    ),

  // --- code provenance ---
  getSessionCommits: (sessionId: string) =>
    getJSON<SessionCommit[]>(`/api/sessions/${sessionId}/commits`),

  searchCommits: (q: string) =>
    getJSON<CommitSearchHit[]>(`/api/commits/search?q=${encodeURIComponent(q)}`),

  // --- mission-control board ---
  getBoard: () => getJSON<BoardSnapshot>("/api/board"),

  respondToSession: (sessionId: string, text: string, submit = true) =>
    sendJSON<{ ok: boolean; pane_id: string }>(
      "POST",
      `/api/sessions/${sessionId}/respond`,
      { text, submit },
    ),

  sendSessionKey: (sessionId: string, key: "escape" | "enter" | "accept") =>
    sendJSON<{ ok: boolean }>("POST", `/api/sessions/${sessionId}/keys`, { key }),

  // --- queued replies (deliver when the turn actually ends) ---
  getQueue: (sessionId: string, signal?: AbortSignal) =>
    getJSON<QueueView>(`/api/sessions/${sessionId}/queue`, signal),

  queueReply: (sessionId: string, text: string, mode: "turn" | "append" = "turn") =>
    sendJSON<QueuedReply>("POST", `/api/sessions/${sessionId}/queue`, { text, mode }),

  // User override: deliver the next queued batch now (skips wait-for-idle).
  sendQueuedNow: (sessionId: string) =>
    sendJSON<{ ok: boolean; sent: number[] }>(
      "POST",
      `/api/sessions/${sessionId}/queue/send-now`,
      {},
    ),

  setQueuedReplyMode: (sessionId: string, qid: number, mode: "turn" | "append") =>
    sendJSON<{ ok: boolean }>("POST", `/api/sessions/${sessionId}/queue/${qid}/mode`, { mode }),

  cancelQueuedReply: (sessionId: string, qid: number) =>
    sendJSON<{ ok: boolean }>("DELETE", `/api/sessions/${sessionId}/queue/${qid}`),

  // --- budget runway + hook telemetry ---
  getRunway: (signal?: AbortSignal) => getJSON<RunwayResponse>("/api/runway", signal),

  getHooksStatus: () => getJSON<HooksStatus>("/api/hooks/status"),

  getSessionSends: (sessionId: string, limit = 20) =>
    getJSON<{ ts: string; action: string; detail: string }[]>(
      `/api/sessions/${sessionId}/sends?limit=${limit}`,
    ),

  getTerminal: (sessionId: string, lines = 30) =>
    getJSON<{ ok: boolean; text: string; error?: string }>(
      `/api/sessions/${sessionId}/terminal?lines=${lines}`,
    ),

  // --- tmux topology (mobile panes view) ---
  // previews=false omits per-pane screen text (~250KB across a fleet) — the
  // task list polls that shape; the deck uses getPaneScreen for live terminals.
  getTmuxLayout: (previews = true, signal?: AbortSignal) =>
    getJSON<TmuxLayout>(`/api/tmux/layout?previews=${previews ? 1 : 0}`, signal),

  getPaneScreen: (paneId: string, signal?: AbortSignal) =>
    getJSON<PaneScreen>(
      `/api/tmux/panes/${encodeURIComponent(paneId)}/screen`,
      signal,
    ),

  // Pane ids look like "%12" — the leading % is URL-encoding's escape char, so it
  // MUST be encoded (→ "%2512") or the server decodes it into a different/invalid id.
  // Slash commands available to a pane's session, for the composer's "/" menu.
  getPaneCommands: (paneId: string, signal?: AbortSignal) =>
    getJSON<SlashCommand[]>(
      `/api/tmux/panes/${encodeURIComponent(paneId)}/commands`,
      signal,
    ),

  sendToPane: (paneId: string, text: string, submit = true) =>
    sendJSON<{ ok: boolean }>(
      "POST",
      `/api/tmux/panes/${encodeURIComponent(paneId)}/send`,
      { text, submit },
    ),

  sendPaneKey: (paneId: string, key: string) =>
    sendJSON<{ ok: boolean }>(
      "POST",
      `/api/tmux/panes/${encodeURIComponent(paneId)}/key`,
      { key },
    ),

  // Cycle Claude Code's permission mode (Shift+Tab); returns where it landed.
  cyclePaneMode: (paneId: string) =>
    sendJSON<{ ok: boolean; mode: string | null }>(
      "POST",
      `/api/tmux/panes/${encodeURIComponent(paneId)}/mode`,
      {},
    ),

  newPaneSession: (cwd?: string) =>
    sendJSON<{ ok: boolean; pane_id: string }>("POST", "/api/tmux/new", { cwd: cwd ?? null }),

  // --- launch profiles (~/.muse/profiles.toml) ---
  listProfiles: () => getJSON<Profile[]>("/api/tmux/profiles"),

  launchProfile: (name: string, values: Record<string, string>, session?: string | null) =>
    sendJSON<{ ok: boolean; pane_id: string }>(
      "POST",
      `/api/tmux/profiles/${encodeURIComponent(name)}/launch`,
      { values, session: session ?? null },
    ),

  launchCodexFromSession: (body: {
    source_session_id: string;
    cwd: string;
    session?: string | null;
    window_name?: string;
    prompt?: string;
  }) =>
    sendJSON<{ ok: boolean; pane_id: string; pack_id: string | null }>(
      "POST",
      "/api/tmux/codex/launch",
      body,
    ),

  // --- session restore (rebuild the tmux layout after a reboot) ---
  getTmuxSnapshot: () => getJSON<LayoutSnapshot>("/api/tmux/snapshot"),

  restoreTmuxLayout: (groups: string[]) =>
    sendJSON<{ ok: boolean; restored: number; skipped: number; groups: string[] }>(
      "POST",
      "/api/tmux/restore",
      { groups },
    ),

  // --- groups (tmux sessions) ---
  // Organize panes by moving whole windows between tmux sessions. Window ids look
  // like "@12" — @ is encoding-safe, but encode anyway to match the pane-id pattern.
  createTmuxSession: (name: string) =>
    sendJSON<{ ok: boolean; session: string }>("POST", "/api/tmux/sessions", { name }),

  renameTmuxSession: (name: string, next: string) =>
    sendJSON<{ ok: boolean; session: string }>(
      "POST",
      `/api/tmux/sessions/${encodeURIComponent(name)}/rename`,
      { name: next },
    ),

  deleteTmuxSession: (name: string) =>
    sendJSON<{ ok: boolean }>("DELETE", `/api/tmux/sessions/${encodeURIComponent(name)}`),

  moveTmuxWindow: (windowId: string, session: string) =>
    sendJSON<{ ok: boolean; window_id: string; session: string }>(
      "POST",
      `/api/tmux/windows/${encodeURIComponent(windowId)}/move`,
      { session },
    ),

  renameTmuxWindow: (windowId: string, name: string) =>
    sendJSON<{ ok: boolean; window_id: string; name: string }>(
      "POST",
      `/api/tmux/windows/${encodeURIComponent(windowId)}/rename`,
      { name },
    ),

  // Removing a session: preview the profile cleanup (if any), then close the window.
  getWindowCleanup: (windowId: string) =>
    getJSON<{
      window_name: string;
      cwd: string;
      cleanup: { profile: string; command: string } | null;
    }>(`/api/tmux/windows/${encodeURIComponent(windowId)}/cleanup`),

  closeTmuxWindow: (windowId: string, cleanup: boolean) =>
    sendJSON<{ ok: boolean; cleanup_ran: boolean; cleanup_ok: boolean; cleanup_output: string }>(
      "POST",
      `/api/tmux/windows/${encodeURIComponent(windowId)}/close`,
      { cleanup },
    ),

  // --- web push notifications ---
  getVapidKey: () => getJSON<{ public_key: string }>("/api/notify/vapid-key"),

  getPushSubscriptions: () =>
    getJSON<PushSubscriptionInfo[]>("/api/notify/subscriptions"),

  addPushSubscription: (sub: {
    endpoint: string;
    keys: { p256dh: string; auth: string };
    label: string;
  }) => sendJSON<{ ok: boolean }>("POST", "/api/notify/subscriptions", sub),

  removePushSubscription: (endpoint: string) =>
    sendJSON<{ ok: boolean }>("DELETE", "/api/notify/subscriptions", { endpoint }),

  // --- pending options (tap-to-select) ---
  getPendingOptions: (sessionId: string, signal?: AbortSignal) =>
    getJSON<PendingOptions>(`/api/sessions/${sessionId}/options`, signal),

  suggestReplies: (sessionId: string) =>
    sendJSON<AIJob>("POST", `/api/sessions/${sessionId}/suggest-replies`),

  // Returns {ok:true} on success, or {ok:false, stale, options} on a 409 stale-menu
  // (the picker re-renders the fresh options instead of acting blindly).
  selectPendingOption: async (
    sessionId: string,
    optionId: string,
    fingerprint: string,
    opts: { method?: "digit" | "arrow"; freeText?: string } = {},
  ): Promise<{ ok: boolean; stale?: boolean; options?: PendingOptions }> => {
    const res = await fetch(`/api/sessions/${sessionId}/options/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        option_id: optionId,
        fingerprint,
        method: opts.method ?? "digit",
        free_text: opts.freeText ?? null,
      }),
    });
    if (res.status === 409) {
      const body = await res.json();
      return { ok: false, stale: true, options: body.options as PendingOptions };
    }
    if (!res.ok) {
      notifyAuthRequired(res.status);
      throw new Error(`${res.status} ${res.statusText} for select`);
    }
    return { ok: true };
  },
};
