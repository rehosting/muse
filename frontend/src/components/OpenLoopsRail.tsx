import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { OpenLoop } from "../api/types";
import ResumeButton from "./ResumeButton";
import { relativeTime } from "../util/format";
import { usePolling } from "../hooks/usePolling";

const NO_RESUME = new Set(["codex", "opencode"]);

/** "Continue working" rail at the top of the session list: recently-active-but-
 * unfinished sessions with their last user intent, open todos/errors, and any
 * 'next' worklog notes (those sort first). Each card resumes or re-opens. */
export default function OpenLoopsRail() {
  const [loops, setLoops] = useState<OpenLoop[] | null>(null);

  const load = useCallback(
    () => api.getOpenLoops().then(setLoops).catch(() => setLoops(null)),
    [],
  );
  usePolling(load, 30000);

  if (!loops || loops.length === 0) return null;

  return (
    <div className="open-loops">
      <h2 className="list-heading">Continue working</h2>
      <div className="open-loops-row">
        {loops.map((l) => (
          <Link
            to={`/sessions/${l.summary.session_id}`}
            className="loop-card"
            key={l.summary.session_id}
          >
            <div className="loop-title" title={l.summary.title}>
              {l.summary.title}
            </div>
            <div className="loop-intent" title={l.last_user_label ?? undefined}>
              {l.last_user_label ?? "—"}
            </div>
            {l.next_notes.length > 0 && (
              <div className="loop-next" title={l.next_notes.map((n) => n.body).join("\n")}>
                ⏭ {l.next_notes[0].body}
              </div>
            )}
            <div className="loop-meta">
              <span className="chip project-chip">
                {(l.summary.project_cwd ?? l.summary.project_dir).split("/").pop()}
              </span>
              <span>{relativeTime(l.summary.mtime)}</span>
              {l.open_todo_count > 0 && <span>○ {l.open_todo_count} todos</span>}
              {l.open_error_count > 0 && (
                <span className="loop-errs">⚠ {l.open_error_count}</span>
              )}
              <span className="loop-spacer" />
              {!NO_RESUME.has(l.summary.provider) && (
                <ResumeButton
                  cwd={l.summary.project_cwd}
                  sessionId={l.summary.session_id}
                  className="card-resume"
                />
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
