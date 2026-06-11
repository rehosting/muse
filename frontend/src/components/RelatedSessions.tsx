import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { RelatedSession } from "../api/types";
import { relativeTime } from "../util/format";

/** A thin bar listing sessions related to this one (same project, shared edited
 * files, temporal adjacency) — the shared files are shown as the explanation. */
export default function RelatedSessions({ sessionId }: { sessionId: string }) {
  const [related, setRelated] = useState<RelatedSession[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let ok = true;
    setRelated([]);
    api
      .getRelatedSessions(sessionId)
      .then((r) => ok && setRelated(r))
      .catch(() => ok && setRelated([]));
    return () => {
      ok = false;
    };
  }, [sessionId]);

  if (related.length === 0) return null;

  return (
    <div className="notes-bar related-bar">
      <button className="notes-toggle" onClick={() => setOpen((o) => !o)}>
        🧬 Related sessions ({related.length}) {open ? "▾" : "▸"}
      </button>
      {!open && (
        <span className="related-inline">
          {related.slice(0, 3).map((r) => (
            <Link
              key={r.summary.session_id}
              to={`/sessions/${r.summary.session_id}`}
              className="backlink-chip related-chip"
              title={
                r.shared_files.length
                  ? `shared: ${r.shared_files.slice(0, 5).join(", ")}`
                  : "same project / time"
              }
            >
              {r.summary.title}
            </Link>
          ))}
        </span>
      )}
      {open && (
        <ul className="notes-list">
          {related.map((r) => (
            <li key={r.summary.session_id} className="note-row">
              <Link to={`/sessions/${r.summary.session_id}`} className="related-title">
                {r.summary.title}
              </Link>
              <Link
                to={`/compare?a=${sessionId}&b=${r.summary.session_id}`}
                className="backlink-ref"
                title="compare side-by-side"
              >
                ⇆ compare
              </Link>
              <span className="note-meta">
                {relativeTime(r.summary.mtime)} · score {r.score}
                {r.same_branch && r.summary.git_branch && (
                  <span title="started on the same git branch"> · ⎇ {r.summary.git_branch}</span>
                )}
              </span>
              {r.shared_files.length > 0 && (
                <span className="note-meta related-files">
                  {r.shared_files
                    .slice(0, 4)
                    .map((f) => f.split("/").pop())
                    .join(" · ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
