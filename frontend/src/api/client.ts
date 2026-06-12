import type {
  AIJob,
  AIStatus,
  Annotations,
  BoardSnapshot,
  CommitSearchHit,
  SessionCommit,
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
  Pack,
  PersistedOutput,
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
} from "./types";

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} for ${url}`);
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
    throw new Error(`${res.status} ${res.statusText} for ${url}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listSessions: () => getJSON<SessionSummary[]>("/api/sessions"),

  getStats: (days = 0) => getJSON<StatsResponse>(`/api/stats?days=${days}`),

  search: (q: string, limit = 30, signal?: AbortSignal) =>
    getJSON<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`, signal),

  getThread: (sessionId: string) =>
    getJSON<Thread>(`/api/sessions/${sessionId}`),

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

  getSessionSends: (sessionId: string, limit = 20) =>
    getJSON<{ ts: string; action: string; detail: string }[]>(
      `/api/sessions/${sessionId}/sends?limit=${limit}`,
    ),

  getTerminal: (sessionId: string, lines = 30) =>
    getJSON<{ ok: boolean; text: string; error?: string }>(
      `/api/sessions/${sessionId}/terminal?lines=${lines}`,
    ),
};
