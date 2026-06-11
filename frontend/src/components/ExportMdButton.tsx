import { useState } from "react";
import { api } from "../api/client";
import {
  downloadMarkdown,
  fetchSubagentTree,
  sessionTreeToMarkdown,
  threadToMarkdown,
} from "../util/exportMarkdown";

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

/** Fetches a session's thread and downloads it as Markdown (for the list page). */
export default function ExportMdButton({
  sessionId,
  title,
  className = "",
}: {
  sessionId: string;
  title: string;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);

  const onClick = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setBusy(true);
    try {
      const thread = await api.getThread(sessionId);
      const subagents = await fetchSubagentTree(sessionId, thread, api.getSubagent);
      const md = subagents.size
        ? sessionTreeToMarkdown(thread, subagents)
        : threadToMarkdown(thread);
      downloadMarkdown(`${slugify(title) || sessionId}.md`, md);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      className={`action-btn ${className}`.trim()}
      title="Export this conversation as Markdown"
      onClick={onClick}
      disabled={busy}
    >
      {busy ? "…" : "↓ MD"}
    </button>
  );
}
