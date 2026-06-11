import { useState } from "react";
import { api } from "../api/client";
import {
  downloadBlob,
  downloadMarkdown,
  fetchSubagentTree,
  sessionTreeToMarkdown,
  threadToMarkdown,
} from "../util/exportMarkdown";
import { sessionToHtml } from "../util/exportHtml";
import { makeRedactor, NOOP_REDACTOR } from "../util/redact";

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

type Format = "md" | "html" | "html-redacted";

/** Export dropdown: Markdown, self-contained HTML, or HTML with best-effort
 * redaction (home paths / emails / secret patterns). Always exports the whole
 * session — subagent transcripts inlined at their spawn points. */
export default function ExportMenu({
  sessionId,
  title,
  className = "",
  label = "↓ MD",
}: {
  sessionId: string;
  title: string;
  className?: string;
  label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const run = async (format: Format, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setOpen(false);
    setBusy(true);
    try {
      const thread = await api.getThread(sessionId);
      const subagents = await fetchSubagentTree(sessionId, thread, api.getSubagent);
      const slug = slugify(title) || sessionId;
      if (format === "md") {
        const md = subagents.size
          ? sessionTreeToMarkdown(thread, subagents)
          : threadToMarkdown(thread);
        downloadMarkdown(`${slug}.md`, md);
      } else {
        const redactor =
          format === "html-redacted"
            ? makeRedactor({ paths: true, emails: true, secrets: true })
            : NOOP_REDACTOR;
        const html = sessionToHtml(thread, subagents, redactor);
        downloadBlob(
          `${slug}${format === "html-redacted" ? ".redacted" : ""}.html`,
          html,
          "text/html;charset=utf-8",
        );
        if (format === "html-redacted") {
          setToast(`${redactor.count()} redactions`);
          setTimeout(() => setToast(null), 4000);
        }
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <span
      className={`export-menu ${className}`.trim()}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
    >
      <button
        className="action-btn"
        title="Export this session (markdown / HTML / redacted HTML)"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        disabled={busy}
      >
        {busy ? "…" : toast ? `✓ ${toast}` : label}
      </button>
      {open && (
        <>
          <div className="menu-overlay" onClick={() => setOpen(false)} />
          <div className="export-pop">
            <button className="export-opt" onClick={(e) => run("md", e)}>
              Markdown (.md)
            </button>
            <button className="export-opt" onClick={(e) => run("html", e)}>
              HTML · self-contained
            </button>
            <button className="export-opt" onClick={(e) => run("html-redacted", e)}>
              HTML · redacted (paths/emails/secrets)
            </button>
          </div>
        </>
      )}
    </span>
  );
}
