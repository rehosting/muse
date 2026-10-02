import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { QueuedReply } from "../../api/types";
import { usePolling } from "../../hooks/usePolling";

/** Pending queued replies for one session: what muse will type in when the turn
 * ends, each cancellable until the moment it's delivered. Recently delivered
 * items flash through briefly (status flips to "sent" and then ages out). */
export default function QueueChips({
  sessionId,
  refreshKey = 0,
  enabled = true,
}: {
  sessionId: string;
  refreshKey?: number; // parent bumps this after queueing to refresh instantly
  enabled?: boolean; // gate polling — deck cards enable only while on screen
}) {
  const [items, setItems] = useState<QueuedReply[]>([]);
  const [holdReason, setHoldReason] = useState<string | null>(null);
  const [sendErr, setSendErr] = useState<string | null>(null);
  const [sendingNow, setSendingNow] = useState(false);

  const refresh = useCallback(async () => {
    const view = await api.getQueue(sessionId);
    setItems(view.items);
    setHoldReason(view.hold_reason);
  }, [sessionId]);

  usePolling(refresh, 5000, enabled);
  useEffect(() => {
    if (!enabled) return;
    refresh().catch(() => {});
  }, [refresh, refreshKey, enabled]);

  const pending = items.filter((q) => q.status === "pending");
  if (pending.length === 0) return null;

  const sendNow = async () => {
    setSendingNow(true);
    setSendErr(null);
    try {
      await api.sendQueuedNow(sessionId);
    } catch (e) {
      // 409 with the block reason (menu open, no pane, …) — show it.
      setSendErr(e instanceof Error ? e.message.replace(/^\d+:\s*/, "") : "couldn’t send");
    } finally {
      setSendingNow(false);
      refresh().catch(() => {});
    }
  };

  const cancel = async (qid: number) => {
    try {
      await api.cancelQueuedReply(sessionId, qid);
    } catch {
      /* already delivered — the refresh below shows the truth */
    }
    refresh().catch(() => {});
  };

  const toggleMode = async (q: QueuedReply) => {
    try {
      await api.setQueuedReplyMode(sessionId, q.id, q.mode === "turn" ? "append" : "turn");
    } catch {
      /* already delivered */
    }
    refresh().catch(() => {});
  };

  return (
    <div className="queue-chips">
      {pending.map((q, i) => (
        <span key={q.id} className="queue-chip" title={q.text}>
          <button
            className={`queue-chip-mode${q.mode === "append" ? " append" : ""}`}
            title={
              q.mode === "append"
                ? "⊕ appends to the previous message — tap for its own turn"
                : "↩ runs as its own turn — tap to append to the previous message"
            }
            disabled={i === 0 && q.mode === "turn" && pending.length === 1}
            onClick={() => toggleMode(q)}
          >
            {q.mode === "append" ? "⊕" : "↩"}
          </button>
          <span className="queue-chip-text">{q.text}</span>
          <button
            className="queue-chip-cancel"
            title="Cancel (won't be sent)"
            onClick={() => cancel(q.id)}
          >
            ✕
          </button>
        </span>
      ))}
      {/* Why it hasn't delivered yet + an explicit override. Without this a held
          reply just sits there silently and reads as "the queue is broken". */}
      {(holdReason || sendErr) && (
        <div className="queue-hold">
          <span className="queue-hold-reason">
            {sendErr ? `⚠ ${sendErr}` : `held — ${holdReason}`}
          </span>
          <button className="queue-hold-send" disabled={sendingNow} onClick={sendNow}>
            {sendingNow ? "sending…" : "send now"}
          </button>
        </div>
      )}
    </div>
  );
}
