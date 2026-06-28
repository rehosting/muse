import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import AiActionButton from "../AiActionButton";

/** Reply to a live Claude session from the board: text → its tmux pane.
 * Disabled (with the reason) when the session has no matched pane. */
export default function ReplyBox({
  sessionId,
  hasPane,
  busy,
  variant = "board",
  draft,
}: {
  sessionId: string;
  hasPane: boolean;
  busy: boolean; // live_status === "busy": sending mid-turn queues the message
  variant?: "board" | "cockpit"; // cockpit = phone-first multiline textarea
  draft?: string; // when set, prefills the box (AI suggestion) — you edit and send
}) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const sentTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(sentTimer.current), []);
  // A parent-supplied draft is a DEFAULT, not an override: apply it only when the
  // box is empty or still holds the previous default (i.e. the user hasn't typed
  // their own text). This lets the suggestion refresh each turn without ever
  // clobbering in-progress input. Send then sends whatever's in the box — so an
  // untouched default sends as-is.
  const lastDraft = useRef("");
  useEffect(() => {
    if (!draft) return;
    setText((cur) => (cur === "" || cur === lastDraft.current ? draft : cur));
    lastDraft.current = draft;
  }, [draft]);

  const send = async () => {
    const t = text.trim();
    if (!t || state === "sending") return;
    setState("sending");
    setError(null);
    try {
      await api.respondToSession(sessionId, t);
      setText("");
      setState("sent");
      sentTimer.current = window.setTimeout(() => setState("idle"), 2500);
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const interrupt = async () => {
    if (!window.confirm("Send Esc to interrupt this session's current turn?")) return;
    try {
      await api.sendSessionKey(sessionId, "escape");
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const placeholder = hasPane
    ? busy
      ? "queue a message for when this turn ends…"
      : "reply to this session…"
    : "no tmux pane matched — open it in your terminal";

  return (
    <div className={variant === "cockpit" ? "reply-box reply-box-cockpit" : "reply-box"}>
      {variant === "cockpit" ? (
        <textarea
          className="reply-input"
          placeholder={placeholder}
          value={text}
          disabled={!hasPane}
          rows={1}
          onChange={(e) => setText(e.target.value)}
        />
      ) : (
        <input
          className="reply-input"
          placeholder={placeholder}
          value={text}
          disabled={!hasPane}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
      )}
      <button
        className="action-btn"
        disabled={!hasPane || !text.trim() || state === "sending"}
        onClick={send}
      >
        {state === "sending" ? "…" : state === "sent" ? "✓ sent" : "Send"}
      </button>
      <button
        className="action-btn reply-esc"
        disabled={!hasPane}
        title="Interrupt the current turn (sends Esc to the pane)"
        onClick={interrupt}
      >
        Esc
      </button>
      {variant !== "cockpit" && (
        <AiActionButton
          label="✦"
          title="AI-draft a reply (prefills the box — you edit and send)"
          enqueue={() => api.draftReply(sessionId)}
          onDone={(job) => {
            const draft = (job.result as { draft?: string } | null)?.draft;
            if (draft) setText(draft);
          }}
        />
      )}
      {state === "error" && error && <div className="reply-error">⚠ {error}</div>}
    </div>
  );
}
