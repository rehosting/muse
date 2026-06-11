import type {
  Annotations,
  AutopilotPolicy,
  AutopilotState,
  AlertEvent,
  AlertRules,
  Bookmark,
  FileChange,
  Investigation,
  InvestigationSummary,
  NotifyConfig,
  NotifyResult,
  PersistedOutput,
  SearchResponse,
  SessionBacklink,
  SessionEvent,
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

  getStats: () => getJSON<StatsResponse>("/api/stats"),

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
};
