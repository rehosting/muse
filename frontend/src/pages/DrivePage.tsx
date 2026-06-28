import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Thread, ThreadItem, ToolResult } from "../api/types";
import AiActionButton from "../components/AiActionButton";
import ConversationView from "../components/ConversationView";
import LiveBadge from "../components/LiveBadge";
import OptionPicker from "../components/OptionPicker";
import ReplyBox from "../components/board/ReplyBox";
import { useSessionStream } from "../hooks/useSessionStream";
import { usePendingOptions } from "../hooks/usePendingOptions";
import { appendFresh, patchResult } from "../util/threadPatch";

const MAX_ITEMS = 120;

/**
 * Phone-first cockpit for ONE live session: read the streaming conversation, tap
 * to select whatever options the agent is presenting, type a free-text reply, or
 * interrupt. Composes the same pieces as FollowPane + the board ReplyBox.
 */
export default function DrivePage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  // Return to wherever we came from (the panes task list / board), not a fixed route.
  const goBack = () => (window.history.length > 1 ? navigate(-1) : navigate("/board"));
  const [thread, setThread] = useState<Thread | null>(null);
  const [live, setLive] = useState(false);
  const liveTimer = useRef<number | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { pending, sending, select } = usePendingOptions(sessionId);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [draft, setDraft] = useState(""); // prefills the composer (top suggestion)
  const suggested = useRef(false);

  // Enqueue suggest_replies, poll the job, prefill the composer with the top one and
  // expose the rest as tap-to-edit chips. Reused by auto-prefill and the ✦ button.
  const fetchSuggestions = useCallback(async () => {
    try {
      let job = await api.suggestReplies(sessionId);
      for (let i = 0; i < 20 && (job.status === "queued" || job.status === "running"); i++) {
        await new Promise((r) => setTimeout(r, 1500));
        job = await api.getAiJob(job.id);
      }
      const s = (job.result as { suggestions?: string[] } | null)?.suggestions ?? [];
      setSuggestions(s);
      if (s[0]) setDraft(s[0]);
    } catch {
      /* ignore — composer still works without suggestions */
    }
  }, [sessionId]);

  // What's the session asking of me right now? Drives the status banner so it's
  // always clear whether to wait, choose, or reply. (pending > busy > idle)
  const status = pending
    ? { cls: "wait", text: "Needs your input — choose below" }
    : live
      ? { cls: "busy", text: "Claude is working — you can queue a reply or interrupt" }
      : { cls: "idle", text: "Your move — edit the suggested reply or write your own" };

  useEffect(() => {
    api
      .getThread(sessionId)
      .then(setThread)
      .catch(() => setThread(null));
  }, [sessionId]);

  const markLive = useCallback(() => {
    setLive(true);
    window.clearTimeout(liveTimer.current);
    liveTimer.current = window.setTimeout(() => setLive(false), 15000);
  }, []);

  const scrollBottom = () =>
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    });

  const onAppend = useCallback(
    (items: ThreadItem[]) => {
      markLive();
      setThread((prev) => (prev ? appendFresh(prev, items) : prev));
      scrollBottom();
    },
    [markLive],
  );
  const onToolResult = useCallback(
    (result: ToolResult) => {
      markLive();
      setThread((prev) => (prev ? patchResult(prev, result) : prev));
    },
    [markLive],
  );
  useSessionStream(sessionId, true, { onAppend, onToolResult });

  useEffect(() => {
    scrollBottom();
  }, [thread?.items.length]);

  // Suggest a default reply each time the session settles into idle/awaiting you.
  // While Claude is working we re-arm, so the next idle (i.e. the next turn ending)
  // fetches a fresh default. ReplyBox only applies it if you haven't typed your own.
  useEffect(() => {
    if (live) {
      suggested.current = false; // working → arm for the next idle
      return;
    }
    if (!suggested.current && !pending) {
      suggested.current = true;
      fetchSuggestions();
    }
  }, [live, pending, fetchSuggestions]);

  return (
    <div className="drive-page">
      <header className="drive-head">
        <button className="drive-back" onClick={goBack} title="Back">
          ‹
        </button>
        <span className="drive-title" title={thread?.title}>
          {thread?.title ?? sessionId.slice(0, 8)}
        </span>
        {live && <LiveBadge />}
        <Link className="drive-full" to={`/sessions/${sessionId}`} title="Open full viewer">
          ↗
        </Link>
      </header>

      <div className="drive-body" ref={scrollRef}>
        {thread ? (
          <ConversationView
            items={thread.items.slice(-MAX_ITEMS)}
            cwd={thread.project_cwd}
            model={null}
            selectedToolId={null}
            onSelectTool={() => {}}
            registerToolRef={() => {}}
            bookmarks={{}}
            onSaveBookmark={() => {}}
            onRemoveBookmark={() => {}}
            compact
            focus
          />
        ) : (
          <div className="empty">Loading…</div>
        )}
      </div>

      <div className="drive-foot">
        <div className={`drive-status drive-status-${status.cls}`}>
          <span className="drive-status-dot" />
          {status.text}
        </div>

        {pending && <OptionPicker pending={pending} sending={sending} onSelect={select} />}

        {suggestions.length > 1 && (
          <div className="option-chips drive-suggestions">
            {suggestions.slice(1).map((s, i) => (
              <button
                key={i}
                className="option-chip"
                onClick={() => setDraft(s)}
                title="Use as draft (edit before sending)"
              >
                <span className="option-chip-label">{s}</span>
              </button>
            ))}
          </div>
        )}

        <div className="drive-composer-row">
          <ReplyBox sessionId={sessionId} hasPane busy={live} variant="cockpit" draft={draft} />
          <AiActionButton
            label="✦"
            className="action-btn drive-suggest-btn"
            title="Re-suggest replies"
            enqueue={() => api.suggestReplies(sessionId)}
            onDone={(job) => {
              const s = (job.result as { suggestions?: string[] } | null)?.suggestions ?? [];
              setSuggestions(s);
              if (s[0]) setDraft(s[0]);
            }}
          />
        </div>
      </div>
    </div>
  );
}
