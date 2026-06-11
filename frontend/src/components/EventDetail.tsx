import type { SessionEvent } from "../api/types";
import Markdown from "./Markdown";

const KIND_LABEL: Record<string, string> = {
  user: "User message",
  assistant_text: "Assistant",
  thinking: "Thinking",
  system: "System",
  lifecycle: "Lifecycle",
  tool_call: "Tool call",
  tool_result: "Tool result",
  subagent: "Subagent",
};

function fmtDur(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60000);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60}m`;
}

/** Detail view for any non-tool timeline event (messages, thinking, system,
 * lifecycle). Tool calls/results are handled by ToolDetail. */
export default function EventDetail({ event }: { event: SessionEvent }) {
  const body = event.detail ?? event.label ?? "";
  // Conversation prose renders as markdown; system/lifecycle stay verbatim.
  const asMarkdown =
    event.kind === "user" || event.kind === "assistant_text" || event.kind === "thinking";

  return (
    <div>
      <div className="detail-head-inline">
        <span className="tool-name">{KIND_LABEL[event.kind] ?? event.kind}</span>
        <code style={{ color: "var(--text-dim)", fontSize: 11 }}>{event.type}</code>
      </div>

      <div className="ev-detail-meta">
        {event.timestamp && (
          <span>{new Date(event.timestamp).toLocaleString([], { hour12: false })}</span>
        )}
        {event.duration_ms != null && <span>· {fmtDur(event.duration_ms)}</span>}
        {event.level && <span className={`ev-level ${event.level}`}>· {event.level}</span>}
        {event.is_error && <span className="ev-level error">· error</span>}
      </div>

      {body.trim() ? (
        asMarkdown ? (
          <div className="cc-md">
            <Markdown>{body}</Markdown>
          </div>
        ) : (
          <pre className="ev-detail-pre">{body}</pre>
        )
      ) : (
        <div className="empty">No additional content for this event.</div>
      )}
    </div>
  );
}
