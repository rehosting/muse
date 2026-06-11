import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ReentryBrief } from "../api/types";
import ResumeButton from "./ResumeButton";

const STALE_SECONDS = 30 * 60; // only show for sessions idle > 30 min

function ago(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

/** "Where you left off" — a collapsible banner shown when re-opening a session
 * that has been idle for a while. Pure aggregation of what muse already knows:
 * the last user goal, open todos, errors since the last turn, recent files, and
 * any AI-written brief. Rows focus-link into the conversation. */
export default function ReentryBanner({
  sessionId,
  provider,
  cwd,
  onFocus,
}: {
  sessionId: string;
  provider: string;
  cwd: string | null;
  onFocus: (uuid: string) => void;
}) {
  const [brief, setBrief] = useState<ReentryBrief | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    let ok = true;
    setBrief(null);
    setDismissed(false);
    api
      .getReentryBrief(sessionId)
      .then((b) => ok && setBrief(b))
      .catch(() => ok && setBrief(null));
    return () => {
      ok = false;
    };
  }, [sessionId]);

  if (
    dismissed ||
    !brief ||
    brief.state !== "stopped" ||
    (brief.idle_seconds ?? 0) < STALE_SECONDS
  ) {
    return null;
  }

  const focusRow = (uuid: string | null) => uuid && onFocus(uuid);

  return (
    <div className="reentry-banner">
      <div className="reentry-head">
        <button className="notes-toggle" onClick={() => setExpanded((e) => !e)}>
          🧭 Where you left off ({ago(brief.idle_seconds ?? 0)} ago) {expanded ? "▾" : "▸"}
        </button>
        <span className="reentry-spacer" />
        {provider !== "codex" && provider !== "opencode" && (
          <ResumeButton cwd={cwd} sessionId={sessionId} />
        )}
        <button className="note-delete reentry-dismiss" title="dismiss" onClick={() => setDismissed(true)}>
          ✕
        </button>
      </div>
      {expanded && (
        <div className="reentry-body">
          {brief.last_goal && (
            <div className="reentry-row reentry-goal" onClick={() => focusRow(brief.last_goal!.anchor_uuid)}>
              <span className="reentry-label">Goal</span>
              <span className="reentry-text">{brief.last_goal.text}</span>
            </div>
          )}
          {brief.last_assistant && (
            <div className="reentry-row" onClick={() => focusRow(brief.last_assistant!.anchor_uuid)}>
              <span className="reentry-label">Last word</span>
              <span className="reentry-text dim">{brief.last_assistant.text}</span>
            </div>
          )}
          {brief.open_todos.length > 0 && (
            <div className="reentry-row">
              <span className="reentry-label">Todos</span>
              <span className="reentry-text">
                {brief.open_todos.map((t, i) => (
                  <span key={i} className={`reentry-todo todo-${t.status}`}>
                    {t.status === "in_progress" ? "▶" : "○"} {t.content}
                  </span>
                ))}
                {brief.done_todos > 0 && (
                  <span className="dim"> (+{brief.done_todos} done)</span>
                )}
              </span>
            </div>
          )}
          {brief.next_notes.length > 0 && (
            <div className="reentry-row">
              <span className="reentry-label">Next</span>
              <span className="reentry-text">
                {brief.next_notes.map((n) => n.body).join(" · ")}
              </span>
            </div>
          )}
          {brief.open_errors.length > 0 && (
            <div className="reentry-row">
              <span className="reentry-label">Errors</span>
              <span className="reentry-text">
                {brief.open_errors.map((e, i) => (
                  <button
                    key={i}
                    className="reentry-err"
                    title={e.detail}
                    onClick={() => focusRow(e.anchor_uuid)}
                  >
                    ⚠ {e.label}
                  </button>
                ))}
              </span>
            </div>
          )}
          {brief.files.length > 0 && (
            <div className="reentry-row">
              <span className="reentry-label">Files</span>
              <span className="reentry-text dim">
                {brief.files.map((f) => f.path.split("/").pop()).join(" · ")}
              </span>
            </div>
          )}
          {brief.latest_ai_brief && (
            <div className="reentry-row">
              <span className="reentry-label">AI brief</span>
              <span className="reentry-text">{brief.latest_ai_brief.body}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
