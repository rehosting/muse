import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { CommitSearchHit, FileActivityGroup, FileHit } from "../api/types";
import { relativeTime } from "../util/format";

const HASH_RE = /^[0-9a-f]{7,40}$/i;

/** Cross-session file history: search any file ever touched by a session, then
 * expand it to see every session that read/edited/wrote it — each op deep-links
 * into the viewer at that exact step (?focus=tool_use_id). */
export default function FilesPage() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<FileHit[] | null>(null);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [activity, setActivity] = useState<FileActivityGroup[] | null>(null);
  const [commitHits, setCommitHits] = useState<CommitSearchHit[] | null>(null);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setHits(null);
      setCommitHits(null);
      return;
    }
    const t = setTimeout(() => {
      api.searchFiles(query).then(setHits).catch(() => setHits([]));
      // A hex string ≥7 chars might be a commit hash → provenance lookup too.
      if (HASH_RE.test(query)) {
        api.searchCommits(query).then(setCommitHits).catch(() => setCommitHits(null));
      } else {
        setCommitHits(null);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    if (!openPath) {
      setActivity(null);
      return;
    }
    let ok = true;
    api
      .getFileActivity(openPath)
      .then((a) => ok && setActivity(a))
      .catch(() => ok && setActivity([]));
    return () => {
      ok = false;
    };
  }, [openPath]);

  return (
    <div className="list-wrap">
      <h2 className="list-heading">File history · which sessions touched a file</h2>
      <input
        className="notes-quick-add journal-add"
        autoFocus
        placeholder="Search by filename, path substring… or a commit hash (≥7 hex chars) to find its source session"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />

      {commitHits && commitHits.length > 0 && (
        <div className="commit-hits">
          <h3 className="journal-session-title">⎘ Commit → likely source session</h3>
          {commitHits.map((c) => (
            <div className="file-session" key={`${c.commit_hash}-${c.session_id}`}>
              <span className={`commit-conf conf-${c.confidence}`}>{c.confidence}</span>
              <code className="commit-hash-inline">{c.commit_hash.slice(0, 10)}</code>
              <span className="commit-subject">{c.subject}</span>
              <Link to={`/sessions/${c.session_id}`} className="file-session-title">
                {c.title ?? c.session_id.slice(0, 8)} →
              </Link>
            </div>
          ))}
        </div>
      )}

      {hits !== null && hits.length === 0 && (commitHits?.length ?? 0) === 0 && (
        <div className="empty">No indexed file activity matches "{q}".</div>
      )}

      {hits?.map((f) => (
        <div className="file-hit" key={f.file_path}>
          <button
            className="file-hit-head"
            onClick={() => setOpenPath(openPath === f.file_path ? null : f.file_path)}
          >
            <span className="file-hit-path">{f.file_path}</span>
            <span className="file-hit-meta">
              {f.session_count} session{f.session_count === 1 ? "" : "s"}
              {" · "}
              {f.reads ?? 0}r / {f.edits ?? 0}e / {f.writes ?? 0}w
              {(f.errors ?? 0) > 0 && <span className="loop-errs"> · ⚠ {f.errors}</span>}
              {f.last_ts && ` · ${relativeTime(f.last_ts)}`}
            </span>
          </button>

          {openPath === f.file_path && (
            <div className="file-hit-body">
              {!activity && <div className="empty">Loading…</div>}
              {activity?.map((g) => (
                <div className="file-session" key={g.session_id}>
                  <Link to={`/sessions/${g.session_id}`} className="file-session-title">
                    {g.title ?? g.session_id.slice(0, 8)}
                  </Link>
                  <span className="note-meta">
                    {g.reads}r / {g.edits}e / {g.writes}w
                    {g.errors > 0 && <span className="loop-errs"> · ⚠ {g.errors}</span>}
                    {g.last_ts && ` · ${relativeTime(g.last_ts)}`}
                  </span>
                  <span className="file-ops">
                    {g.ops
                      .filter((o) => o.tool_use_id)
                      .slice(0, 8)
                      .map((o, i) => (
                        <Link
                          key={i}
                          className={`file-op file-op-${o.op}${o.is_error ? " file-op-err" : ""}`}
                          to={`/sessions/${g.session_id}?focus=${o.tool_use_id}`}
                          title={`${o.op}${o.ts ? ` @ ${o.ts}` : ""} — open at this step`}
                        >
                          {o.op === "read" ? "👁" : o.op === "write" ? "✏️" : "✎"}
                        </Link>
                      ))}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
