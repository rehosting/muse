import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SessionSummary } from "../api/types";
import LiveBadge from "../components/LiveBadge";
import ResumeButton from "../components/ResumeButton";
import ExportMdButton from "../components/ExportMdButton";
import OpenLoopsRail from "../components/OpenLoopsRail";
import { formatBytes, formatTokens, relativeTime, shortModel } from "../util/format";
import { usePolling } from "../hooks/usePolling";

const PROVIDER_LABEL: Record<string, string> = {
  claude: "Claude",
  codex: "Codex",
  gemini: "Gemini",
  opencode: "opencode",
};

// Providers muse can't relaunch from a session id (read-only investigation only).
const NO_RESUME = new Set(["codex", "opencode"]);

export default function SessionListPage() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () => api.listSessions().then(setSessions).catch((e) => setError(String(e))),
    [],
  );
  usePolling(load, 6000);

  if (error)
    return (
      <div className="list-wrap">
        <div className="error-banner">{error}</div>
      </div>
    );
  if (!sessions)
    return (
      <div className="list-wrap">
        <div className="empty">Loading sessions…</div>
      </div>
    );
  if (sessions.length === 0)
    return (
      <div className="list-wrap">
        <div className="empty">No sessions found.</div>
      </div>
    );

  // Backend returns sessions sorted by mtime desc; keep that flat time order.
  return (
    <div className="list-wrap">
      <OpenLoopsRail />
      <h2 className="list-heading">All sessions · most recent first</h2>
      {sessions.map((s) => (
        <Link to={`/sessions/${s.session_id}`} className="session-card" key={s.session_id}>
          <div className="session-card-top">
            <span className={`provider-badge provider-${s.provider}`}>
              {PROVIDER_LABEL[s.provider] ?? s.provider}
            </span>
            <div className="title">{s.title}</div>
            {s.is_running && <LiveBadge />}
            {s.health && s.health !== "ok" && (
              <span
                className={`health-chip health-${s.health}`}
                title={`Failure patterns detected — ${s.health}`}
              >
                {s.health === "bad" ? "🔴" : "🟡"}
              </span>
            )}
            <ExportMdButton sessionId={s.session_id} title={s.title} className="card-resume" />
            {!NO_RESUME.has(s.provider) && (
              <ResumeButton cwd={s.project_cwd} sessionId={s.session_id} className="card-resume" />
            )}
          </div>
          <div className="session-meta">
            <span className="chip project-chip">{s.project_cwd ?? s.project_dir}</span>
            <span>{relativeTime(s.mtime)}</span>
            <span>{s.message_count} msgs</span>
            {s.total_tokens > 0 && (
              <span className="tok-chip" title={`${s.total_tokens.toLocaleString()} tokens`}>
                {formatTokens(s.total_tokens)} tok
              </span>
            )}
            {s.subagent_count > 0 && <span>{s.subagent_count} subagents</span>}
            {s.model && <span className="chip">{shortModel(s.model)}</span>}
            {s.git_branch && <span className="chip">{s.git_branch}</span>}
            <span>{formatBytes(s.size_bytes)}</span>
          </div>
        </Link>
      ))}
    </div>
  );
}
