import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
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
  const [params, setParams] = useSearchParams();
  const branch = params.get("branch");
  const project = params.get("project");

  const load = useCallback(
    () => api.listSessions().then(setSessions).catch((e) => setError(String(e))),
    [],
  );
  usePolling(load, 6000);

  // Branch/project chips on the cards toggle these URL-param filters.
  const setFilter = (key: "branch" | "project", value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const chipFilter =
    (key: "branch" | "project", value: string) =>
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setFilter(key, value);
    };

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

  const filtered = sessions.filter(
    (s) =>
      (!branch || s.git_branch === branch) &&
      (!project || (s.project_cwd ?? s.project_dir) === project),
  );

  // Backend returns sessions sorted by mtime desc; keep that flat time order.
  return (
    <div className="list-wrap">
      {!branch && !project && <OpenLoopsRail />}
      <div className="list-head-row">
        <h2 className="list-heading">
          {branch || project ? `${filtered.length} sessions` : "All sessions · most recent first"}
        </h2>
        <button
          className="action-btn primary new-session-btn"
          onClick={() => window.dispatchEvent(new CustomEvent("muse:launch", { detail: {} }))}
        >
          ✻ New session
        </button>
        {branch && (
          <button className="filter-chip" onClick={() => setFilter("branch", null)}>
            ⎇ {branch} ✕
          </button>
        )}
        {project && (
          <button className="filter-chip" onClick={() => setFilter("project", null)}>
            📁 {project.split("/").pop()} ✕
          </button>
        )}
      </div>
      {filtered.map((s) => (
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
            <button
              className="chip project-chip chip-btn"
              title="Filter by this project"
              onClick={chipFilter("project", s.project_cwd ?? s.project_dir)}
            >
              {s.project_cwd ?? s.project_dir}
            </button>
            <span>{relativeTime(s.mtime)}</span>
            <span>{s.message_count} msgs</span>
            {s.total_tokens > 0 && (
              <span className="tok-chip" title={`${s.total_tokens.toLocaleString()} tokens`}>
                {formatTokens(s.total_tokens)} tok
              </span>
            )}
            {s.subagent_count > 0 && <span>{s.subagent_count} subagents</span>}
            {s.model && <span className="chip">{shortModel(s.model)}</span>}
            {s.git_branch && (
              <button
                className="chip chip-btn"
                title="Filter by this branch (as recorded at session start)"
                onClick={chipFilter("branch", s.git_branch)}
              >
                ⎇ {s.git_branch}
              </button>
            )}
            <span>{formatBytes(s.size_bytes)}</span>
          </div>
        </Link>
      ))}
      {filtered.length === 0 && (
        <div className="empty">No sessions match the active filter.</div>
      )}
    </div>
  );
}
