import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SessionSummary } from "../api/types";
import ResumeButton from "../components/ResumeButton";
import StateBadge from "../components/StateBadge";
import { relativeTime, shortModel } from "../util/format";
import { usePolling } from "../hooks/usePolling";

const STATE_ORDER: Record<SessionSummary["state"], number> = { live: 0, waiting: 1, stopped: 2 };

/** Live-updating monitor: sessions sorted by state (live → waiting → stopped),
 * then by most recent output. Select sessions to follow them together. */
export default function BoardPage() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const navigate = useNavigate();

  const load = useCallback(
    () => api.listSessions().then(setSessions).catch((e) => setError(String(e))),
    [],
  );
  usePolling(load, 3000);

  const sorted = useMemo(() => {
    if (!sessions) return [];
    return [...sessions].sort(
      (a, b) =>
        STATE_ORDER[a.state] - STATE_ORDER[b.state] || b.mtime.localeCompare(a.mtime),
    );
  }, [sessions]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const follow = () => {
    if (selected.size) navigate(`/follow?sessions=${[...selected].join(",")}`);
  };

  const liveIds = (sessions ?? []).filter((s) => s.state === "live").map((s) => s.session_id);
  const followAllLive = () => {
    if (liveIds.length) navigate(`/follow?sessions=${liveIds.join(",")}`);
  };

  if (error)
    return (
      <div className="list-wrap">
        <div className="error-banner">{error}</div>
      </div>
    );
  if (!sessions) return <div className="list-wrap"><div className="empty">Loading…</div></div>;

  const counts = {
    live: sessions.filter((s) => s.state === "live").length,
    waiting: sessions.filter((s) => s.state === "waiting").length,
    stopped: sessions.filter((s) => s.state === "stopped").length,
  };

  return (
    <div className="list-wrap">
      <div className="board-head">
        <h2 className="list-heading">
          Monitor · <span className="state-live-txt">{counts.live} live</span> ·{" "}
          <span className="state-waiting-txt">{counts.waiting} waiting</span> ·{" "}
          <span className="state-stopped-txt">{counts.stopped} stopped</span>
        </h2>
        <div className="board-actions">
          <button
            className="action-btn follow-live-btn"
            disabled={!liveIds.length}
            onClick={followAllLive}
            title="Open a live-tailing pane for every live session"
          >
            ● Follow all live ({liveIds.length})
          </button>
          <button className="action-btn follow-btn" disabled={!selected.size} onClick={follow}>
            Follow selected ({selected.size}) →
          </button>
        </div>
      </div>

      <table className="board-table">
        <tbody>
          {sorted.map((s) => (
            <tr key={s.session_id} className={`board-row board-${s.state}`}>
              <td className="board-check">
                <input
                  type="checkbox"
                  checked={selected.has(s.session_id)}
                  onChange={() => toggle(s.session_id)}
                />
              </td>
              <td className="board-state">
                <StateBadge state={s.state} />
              </td>
              <td className="board-title">
                <Link to={`/sessions/${s.session_id}`}>{s.title}</Link>
                <div className="board-project">{s.project_cwd ?? s.project_dir}</div>
              </td>
              <td className="board-time">{relativeTime(s.mtime)}</td>
              <td className="board-model">{s.model ? shortModel(s.model) : ""}</td>
              <td className="board-actions">
                <ResumeButton cwd={s.project_cwd} sessionId={s.session_id} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
