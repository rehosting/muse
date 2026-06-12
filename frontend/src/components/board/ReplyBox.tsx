import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";

/** Reply to a live Claude session from the board: text → its tmux pane.
 * Disabled (with the reason) when the session has no matched pane. */
export default function ReplyBox({
  sessionId,
  hasPane,
  busy,
}: {
  sessionId: string;
  hasPane: boolean;
  busy: boolean; // live_status === "busy": sending mid-turn queues the message
}) {
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const sentTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(sentTimer.current), []);

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

  return (
    <div className="reply-box">
      <input
        className="reply-input"
        placeholder={
          hasPane
            ? busy
              ? "queue a message for when this turn ends…"
              : "reply to this session…"
            : "no tmux pane matched — open it in your terminal"
        }
        value={text}
        disabled={!hasPane}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") send();
        }}
      />
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
      {state === "error" && error && <div className="reply-error">⚠ {error}</div>}
    </div>
  );
}
