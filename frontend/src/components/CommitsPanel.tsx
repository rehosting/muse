import { useState } from "react";
import type { SessionCommit } from "../api/types";

const CONF_ICON: Record<string, string> = { high: "●", medium: "◐", low: "○" };

function basisLabel(c: SessionCommit): string {
  const b = c.basis;
  const parts: string[] = [];
  if (b.in_window) parts.push("during session");
  if (b.slack) parts.push("just after");
  if (b.coverage != null) parts.push(`${Math.round(b.coverage * 100)}% file overlap`);
  if (b.branch_match) parts.push(`branch ${b.branch_match}`);
  return parts.join(" · ");
}

/** Commits this session likely produced — evidence-based (time window + file
 * overlap + branch), surfaced with explicit confidence; never claimed as
 * authorship. Click a hash to copy it; expand to see the changed files. */
export default function CommitsPanel({
  commits,
  projectCwd,
}: {
  commits: SessionCommit[];
  projectCwd: string | null;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  if (commits.length === 0) {
    return (
      <div className="empty">
        No commits linked — the repo may not be harvested yet, or nothing was
        committed during this session's window.
      </div>
    );
  }

  const copy = (hash: string) => {
    navigator.clipboard?.writeText(hash).then(() => {
      setCopied(hash);
      setTimeout(() => setCopied(null), 1500);
    });
  };

  return (
    <div className="commits-panel">
      <div className="commits-note dim">
        Linked by evidence (time + files + branch) — not authorship proof.
      </div>
      {commits.map((c) => (
        <div key={c.commit_hash} className="commit-row">
          <div className="commit-top">
            <span className={`commit-conf conf-${c.confidence}`} title={`${c.confidence} confidence — ${basisLabel(c)}`}>
              {CONF_ICON[c.confidence] ?? "○"}
            </span>
            <button
              className="commit-hash"
              title="Copy full hash"
              onClick={() => copy(c.commit_hash)}
            >
              {copied === c.commit_hash ? "copied!" : c.commit_hash.slice(0, 10)}
            </button>
            <span className="commit-subject" title={c.subject ?? ""}>
              {c.subject}
            </span>
            <button
              className="commit-files-toggle dim"
              onClick={() => setOpen(open === c.commit_hash ? null : c.commit_hash)}
            >
              {c.file_count} file{c.file_count === 1 ? "" : "s"} {open === c.commit_hash ? "▾" : "▸"}
            </button>
          </div>
          <div className="commit-meta dim">
            {c.committer_date && new Date(c.committer_date).toLocaleString()}
            {" · "}
            {basisLabel(c)}
          </div>
          {open === c.commit_hash && (
            <div className="commit-files">
              {c.files.map((f) => (
                <div
                  key={f}
                  className={`commit-file${(c.basis.shared_files ?? []).includes(f) ? " shared" : ""}`}
                  title={(c.basis.shared_files ?? []).includes(f) ? "this session also edited this file" : undefined}
                >
                  {f}
                </div>
              ))}
              {c.file_count > c.files.length && (
                <div className="dim">… +{c.file_count - c.files.length} more</div>
              )}
              <div className="commit-show dim">
                inspect: <code>git -C {projectCwd ?? "<repo>"} show {c.commit_hash.slice(0, 10)}</code>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
