import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Thread, ThreadItem, ToolResult } from "../api/types";
import ConversationView from "./ConversationView";
import LiveBadge from "./LiveBadge";
import { useSessionStream } from "../hooks/useSessionStream";
import { appendFresh, patchResult } from "../util/threadPatch";

const MAX_ITEMS = 60; // keep panes light

/** A single live-tailing conversation pane for the multi-session follow view. */
export default function FollowPane({
  sessionId,
  onRemove,
}: {
  sessionId: string;
  onRemove: (id: string) => void;
}) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [live, setLive] = useState(false);
  const liveTimer = useRef<number | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getThread(sessionId).then(setThread).catch(() => setThread(null));
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

  return (
    <div className="follow-pane">
      <div className="follow-pane-head">
        <span className="follow-pane-title" title={thread?.title}>
          {thread?.title ?? sessionId.slice(0, 8)}
        </span>
        {live && <LiveBadge />}
        <Link className="follow-pane-link" to={`/sessions/${sessionId}`} title="Open full viewer">
          ↗
        </Link>
        <button className="follow-pane-close" onClick={() => onRemove(sessionId)} title="Remove">
          ✕
        </button>
      </div>
      <div className="follow-pane-body" ref={scrollRef}>
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
          />
        ) : (
          <div className="empty">Loading…</div>
        )}
      </div>
    </div>
  );
}
