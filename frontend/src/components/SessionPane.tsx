import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Thread, ThreadItem, ToolResult } from "../api/types";
import ConversationView from "./ConversationView";
import LiveBadge from "./LiveBadge";
import { useSessionStream } from "../hooks/useSessionStream";
import { appendFresh, patchResult } from "../util/threadPatch";

/** Right-hand pane of the investigation split view: renders one session's
 * conversation and scrolls to the referenced step when `focusAnchor` changes.
 * Live-tails the session (it may still be running), but does NOT auto-scroll to
 * the bottom while a reference is pinned, so clicking a citation stays put. */
export default function SessionPane({
  sessionId,
  focusAnchor,
}: {
  sessionId: string | null;
  focusAnchor: string | null;
}) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const liveTimer = useRef<number | undefined>(undefined);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const toolRefs = useRef<Map<string, HTMLElement>>(new Map());
  const scrollRef = useRef<HTMLDivElement>(null);
  const doneFocus = useRef<string | null>(null);

  useEffect(() => {
    itemRefs.current.clear();
    toolRefs.current.clear();
    doneFocus.current = null;
    setThread(null);
    setSelectedToolId(null);
    if (!sessionId) return;
    let cancelled = false;
    api
      .getThread(sessionId)
      .then((t) => !cancelled && setThread(t))
      .catch(() => !cancelled && setThread(null));
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const markLive = useCallback(() => {
    setLive(true);
    window.clearTimeout(liveTimer.current);
    liveTimer.current = window.setTimeout(() => setLive(false), 15000);
  }, []);
  const onAppend = useCallback(
    (items: ThreadItem[]) => {
      markLive();
      setThread((prev) => (prev ? appendFresh(prev, items) : prev));
      if (!focusAnchor) {
        requestAnimationFrame(() => {
          const el = scrollRef.current;
          if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
        });
      }
    },
    [markLive, focusAnchor],
  );
  const onToolResult = useCallback(
    (result: ToolResult) => {
      markLive();
      setThread((prev) => (prev ? patchResult(prev, result) : prev));
    },
    [markLive],
  );
  useSessionStream(sessionId ?? "", !!sessionId, { onAppend, onToolResult });

  const registerItem = useCallback((uuid: string, el: HTMLElement | null) => {
    if (el) itemRefs.current.set(uuid, el);
    else itemRefs.current.delete(uuid);
  }, []);
  const registerTool = useCallback((id: string, el: HTMLElement | null) => {
    if (el) toolRefs.current.set(id, el);
    else toolRefs.current.delete(id);
  }, []);

  // Scroll to the referenced step once the thread has rendered. A tool anchor is
  // selected + scrolled; a message/item anchor is scrolled into view.
  useEffect(() => {
    if (!thread || !focusAnchor) return;
    const key = `${sessionId}:${focusAnchor}`;
    if (doneFocus.current === key) return;
    const raf = requestAnimationFrame(() => {
      const tool = toolRefs.current.get(focusAnchor);
      if (tool) {
        setSelectedToolId(focusAnchor);
        tool.scrollIntoView({ block: "center", behavior: "smooth" });
        doneFocus.current = key;
        return;
      }
      const item = itemRefs.current.get(focusAnchor);
      if (item) {
        item.scrollIntoView({ block: "center", behavior: "smooth" });
        doneFocus.current = key;
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [thread, focusAnchor, sessionId]);

  if (!sessionId) {
    return (
      <div className="session-pane">
        <div className="session-pane-empty">
          Select a reference on the left to view that step here.
        </div>
      </div>
    );
  }

  return (
    <div className="session-pane">
      <div className="session-pane-head">
        <span className="session-pane-title" title={thread?.title}>
          {thread?.title ?? sessionId.slice(0, 8)}
        </span>
        {live && <LiveBadge />}
        <Link
          className="session-pane-link"
          to={`/sessions/${sessionId}${focusAnchor ? `?focus=${focusAnchor}` : ""}`}
          title="Open full viewer"
        >
          ↗
        </Link>
      </div>
      <div className="session-pane-body" ref={scrollRef}>
        {thread ? (
          <ConversationView
            items={thread.items}
            cwd={thread.project_cwd}
            model={thread.model}
            version={thread.version}
            contextWindow={thread.context_window ?? undefined}
            provider={thread.provider}
            selectedToolId={selectedToolId}
            onSelectTool={(id) => setSelectedToolId(id)}
            registerToolRef={registerTool}
            registerItemRef={registerItem}
            bookmarks={{}}
            onSaveBookmark={() => {}}
            onRemoveBookmark={() => {}}
            compact
            highlightUuid={focusAnchor}
          />
        ) : (
          <div className="empty">Loading…</div>
        )}
      </div>
    </div>
  );
}
