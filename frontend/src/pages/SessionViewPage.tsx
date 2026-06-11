import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  FileChange,
  SessionEvent,
  SessionLineage,
  Thread,
  ThreadItem,
  ToolResult,
} from "../api/types";
import type { Crumb } from "../components/Breadcrumb";
import ConversationView, { type SelectSource } from "../components/ConversationView";
import EventTimeline from "../components/EventTimeline";
import EventDetail from "../components/EventDetail";
import FileChanges from "../components/FileChanges";
import ToolDetail from "../components/ToolDetail";
import ToolDetailPanel from "../components/ToolDetailPanel";
import ViewerHeader, { type LayoutMode } from "../components/ViewerHeader";
import SessionBacklinks from "../components/SessionBacklinks";
import NotesPanel from "../components/NotesPanel";
import ReentryBanner from "../components/ReentryBanner";
import RelatedSessions from "../components/RelatedSessions";
import HealthBar from "../components/HealthBar";
import { type SubNode } from "../components/SubagentTree";
import ResizableSplit from "../components/ResizableSplit";
import { useSessionStream } from "../hooks/useSessionStream";
import { toolMap } from "../util/toolIndex";
import { sessionStats } from "../util/stats";

export default function SessionViewPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const agentStack = useMemo(() => {
    const raw = searchParams.get("agent");
    return raw ? raw.split(",").filter(Boolean) : [];
  }, [searchParams]);

  const layout = (Number(searchParams.get("view")) || 3) as LayoutMode;

  const [main, setMain] = useState<Thread | null>(null);
  const [subThreads, setSubThreads] = useState<Record<string, Thread>>({});
  const [error, setError] = useState<string | null>(null);

  // "live" means we've actually seen streamed activity recently — not merely
  // that the SSE socket is open (which is true for historical sessions too).
  const [live, setLive] = useState(false);
  const liveTimer = useRef<number | undefined>(undefined);
  const [scrollNonce, setScrollNonce] = useState(0);
  const convScrollRef = useRef<HTMLDivElement>(null);
  const logScrollRef = useRef<HTMLDivElement>(null);

  // Annotations (renames + bookmarks) live in muse's own DB, keyed by uuid.
  const [bookmarks, setBookmarks] = useState<Record<string, string>>({});
  // Compaction lineage (main thread only).
  const [lineage, setLineage] = useState<SessionLineage | null>(null);

  // Selection drives both the detail pane and cross-pane scroll syncing.
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);
  // Non-tool timeline event shown in the Detail pane (messages, thinking, etc.).
  const [selectedEvent, setSelectedEvent] = useState<SessionEvent | null>(null);
  const [selectSource, setSelectSource] = useState<SelectSource | null>(null);
  const [selectNonce, setSelectNonce] = useState(0);
  const [overlayOpen, setOverlayOpen] = useState(false);

  const convToolRefs = useRef<Map<string, HTMLElement>>(new Map());
  const convItemRefs = useRef<Map<string, HTMLElement>>(new Map());

  // Complete event timeline for the currently-viewed thread/subagent.
  const [events, setEvents] = useState<SessionEvent[]>([]);
  // Per-file activity for the currently-viewed thread/subagent.
  const [files, setFiles] = useState<FileChange[]>([]);
  // Which view the middle panel shows, and a signal to force errors-only mode.
  const [panelTab, setPanelTab] = useState<"timeline" | "files">("timeline");
  const [errSignal, setErrSignal] = useState(0);

  // ---- data loading ----
  useEffect(() => {
    if (!sessionId) return;
    setMain(null);
    setSelectedToolId(null);
    setLineage(null);
    api.getThread(sessionId).then(setMain).catch((e) => setError(String(e)));
    api.getLineage(sessionId).then(setLineage).catch(() => setLineage(null));
    api
      .getAnnotations(sessionId)
      .then((a) => {
        const map: Record<string, string> = {};
        for (const b of a.bookmarks) map[b.message_uuid] = b.note;
        setBookmarks(map);
      })
      .catch(() => setBookmarks({}));
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    for (const agentId of agentStack) {
      if (!subThreads[agentId]) {
        api
          .getSubagent(sessionId, agentId)
          .then((t) => setSubThreads((prev) => ({ ...prev, [agentId]: t })))
          .catch((e) => setError(String(e)));
      }
    }
  }, [sessionId, agentStack, subThreads]);

  // Load the timeline + per-file activity for whichever thread/subagent is shown.
  useEffect(() => {
    if (!sessionId) return;
    const agentId = agentStack.length ? agentStack[agentStack.length - 1] : undefined;
    setEvents([]);
    setFiles([]);
    api.getEvents(sessionId, agentId).then(setEvents).catch(() => setEvents([]));
    api.getFiles(sessionId, agentId).then(setFiles).catch(() => setFiles([]));
  }, [sessionId, agentStack]);

  const errorCount = useMemo(() => events.filter((e) => e.is_error).length, [events]);

  // ---- live streaming (applies to the main thread) ----
  const markLive = useCallback(() => {
    setLive(true);
    window.clearTimeout(liveTimer.current);
    liveTimer.current = window.setTimeout(() => setLive(false), 15000);
  }, []);

  const onAppend = useCallback(
    (items: ThreadItem[]) => {
      markLive();
      setMain((prev) => {
        if (!prev) return prev;
        const seen = new Set(prev.items.map((i) => i.uuid));
        const fresh = items.filter((i) => !i.is_sidechain && !seen.has(i.uuid));
        if (!fresh.length) return prev;
        setScrollNonce((n) => n + 1); // request auto-scroll to bottom
        return { ...prev, items: [...prev.items, ...fresh] };
      });
    },
    [markLive],
  );
  const onToolResult = useCallback(
    (result: ToolResult) => {
      markLive();
      setMain((prev) => (prev ? patchResult(prev, result) : prev));
    },
    [markLive],
  );
  useSessionStream(sessionId, true, { onAppend, onToolResult });

  // Auto-scroll both the conversation and the tool log to the bottom as live
  // activity streams in.
  useEffect(() => {
    if (scrollNonce === 0) return;
    requestAnimationFrame(() => {
      for (const el of [convScrollRef.current, logScrollRef.current]) {
        el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      }
    });
  }, [scrollNonce]);

  // ---- annotation handlers (write to muse's DB; ~/.claude untouched) ----
  const saveBookmark = useCallback(
    (uuid: string, note: string) => {
      if (!sessionId) return;
      setBookmarks((prev) => ({ ...prev, [uuid]: note }));
      api.upsertBookmark(sessionId, uuid, note).catch(() => {});
    },
    [sessionId],
  );
  const removeBookmark = useCallback(
    (uuid: string) => {
      if (!sessionId) return;
      setBookmarks((prev) => {
        const next = { ...prev };
        delete next[uuid];
        return next;
      });
      api.deleteBookmark(sessionId, uuid).catch(() => {});
    },
    [sessionId],
  );
  const renameSession = useCallback(
    (title: string) => {
      if (!sessionId) return;
      const clean = title.trim();
      setMain((prev) => (prev ? { ...prev, title: clean || prev.title } : prev));
      api.setTitle(sessionId, clean || null).catch(() => {});
    },
    [sessionId],
  );

  // ---- current thread (main or deepest subagent) ----
  const current: Thread | null =
    agentStack.length > 0 ? subThreads[agentStack[agentStack.length - 1]] ?? null : main;

  const toolsById = useMemo(() => (current ? toolMap(current.items) : new Map()), [current]);
  const ctxWindow = useMemo(
    () => (current ? sessionStats(current).contextWindow : 200_000),
    [current],
  );
  const selectedTool = selectedToolId ? toolsById.get(selectedToolId) ?? null : null;

  // ---- selection + cross-pane scroll sync ----
  const selectTool = useCallback(
    (id: string, source: SelectSource) => {
      setSelectedEvent(null);
      setSelectedToolId(id);
      setSelectSource(source);
      setSelectNonce((n) => n + 1);
      if (layout === 1) setOverlayOpen(true);
    },
    [layout],
  );

  // Deep link: ?focus=<message uuid | tool_use_id> selects + scrolls on load.
  const focus = searchParams.get("focus");
  const focusedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || !current || events.length === 0) return;
    if (focusedRef.current === focus) return;
    focusedRef.current = focus;
    requestAnimationFrame(() => {
      if (toolsById.has(focus)) selectTool(focus, "log");
      else convItemRefs.current.get(focus)?.scrollIntoView({ block: "center" });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, current, events.length]);

  // Global shortcuts: "/" focuses conversation search, "?" toggles help.
  const [showHelp, setShowHelp] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA");
      if (e.key === "Escape") return setShowHelp(false);
      if (typing) return;
      if (e.key === "/") {
        e.preventDefault();
        (document.querySelector(".cc-search-input") as HTMLInputElement | null)?.focus();
      } else if (e.key === "?") {
        e.preventDefault();
        setShowHelp((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!selectedToolId) return;
    // Scroll the conversation to the selected tool (the timeline scrolls itself
    // to a selected tool via its own selectedToolId effect).
    if (selectSource !== "conversation") {
      requestAnimationFrame(() => {
        convToolRefs.current.get(selectedToolId)?.scrollIntoView({
          block: "center",
          behavior: "smooth",
        });
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectNonce]);

  // ---- navigation helpers ----
  const setLayout = (mode: LayoutMode) => {
    const next = new URLSearchParams(searchParams);
    next.set("view", String(mode));
    setSearchParams(next);
  };
  const navigateTo = (index: number) => {
    const newStack = agentStack.slice(0, index);
    setSelectedToolId(null);
    setOverlayOpen(false);
    const next = new URLSearchParams(searchParams);
    if (newStack.length) next.set("agent", newStack.join(","));
    else next.delete("agent");
    setSearchParams(next);
  };
  const openSubagent = (agentId: string) => {
    setSelectedToolId(null);
    setOverlayOpen(false);
    const next = new URLSearchParams(searchParams);
    next.set("agent", [...agentStack, agentId].join(","));
    setSearchParams(next);
  };
  const setAgentPath = (path: string[]) => {
    setSelectedToolId(null);
    setOverlayOpen(false);
    const next = new URLSearchParams(searchParams);
    if (path.length) next.set("agent", path.join(","));
    else next.delete("agent");
    setSearchParams(next);
  };

  // Subagent tree: top-level from the main thread, nested levels filled in from
  // any subagent threads already loaded (visiting a subagent reveals its children).
  const subagentTree = useMemo<SubNode[]>(() => {
    const build = (thread: Thread | null | undefined, parentPath: string[]): SubNode[] => {
      if (!thread) return [];
      const nodes: SubNode[] = [];
      for (const item of thread.items) {
        for (const b of item.blocks) {
          if (b.tool_use?.subagent) {
            const sa = b.tool_use.subagent;
            const path = [...parentPath, sa.agent_id];
            nodes.push({
              agentId: sa.agent_id,
              agentType: sa.agent_type,
              description: sa.description,
              path,
              children: build(subThreads[sa.agent_id], path),
            });
          }
        }
      }
      return nodes;
    };
    return build(main, []);
  }, [main, subThreads]);

  const subagentCount = useMemo(() => {
    let c = 0;
    const walk = (ns: SubNode[]) => ns.forEach((n) => ((c += 1), walk(n.children)));
    walk(subagentTree);
    return c;
  }, [subagentTree]);

  const crumbs: Crumb[] = useMemo(() => {
    const out: Crumb[] = [{ label: main?.title ?? "session" }];
    agentStack.forEach((id) => {
      const t = subThreads[id];
      out.push({ label: t?.agent_type ?? "subagent", sub: t?.description ?? undefined });
    });
    return out;
  }, [main, agentStack, subThreads]);

  const registerConvRef = useCallback((id: string, el: HTMLElement | null) => {
    if (el) convToolRefs.current.set(id, el);
    else convToolRefs.current.delete(id);
  }, []);
  const registerConvItemRef = useCallback((uuid: string, el: HTMLElement | null) => {
    if (el) convItemRefs.current.set(uuid, el);
    else convItemRefs.current.delete(uuid);
  }, []);

  // Timeline → viewer: subagent spawns drill in; tool calls/results open the
  // tool detail (and sync); every other entry opens in the Detail pane too, and
  // also scrolls the conversation to that entry.
  const onSelectEvent = useCallback(
    (ev: SessionEvent) => {
      if (ev.kind === "subagent" && ev.subagent) {
        openSubagent(ev.subagent.agent_id);
        return;
      }
      if (ev.tool_use_id && toolsById.has(ev.tool_use_id)) {
        selectTool(ev.tool_use_id, "log");
        return;
      }
      setSelectedToolId(null);
      setSelectedEvent(ev);
      if (layout === 1) setOverlayOpen(true);
      if (ev.anchor_uuid) {
        convItemRefs.current
          .get(ev.anchor_uuid)
          ?.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectTool, toolsById, layout],
  );

  if (error) return <div className="error-banner">{error}</div>;
  if (!main || !current) return <div className="empty">Loading session…</div>;

  const jumpToBottom = () => {
    const el = convScrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };
  const conversation = (
    <div className="panel panel-conversation">
      <div className="panel-label">Conversation</div>
      <div className="panel-scroll" ref={convScrollRef}>
        <ConversationView
          items={current.items}
          cwd={current.project_cwd}
          model={current.model ?? firstModel(current)}
          version={current.version}
          contextWindow={current.context_window ?? ctxWindow}
          provider={current.provider}
          selectedToolId={selectedToolId}
          onSelectTool={selectTool}
          registerToolRef={registerConvRef}
          registerItemRef={registerConvItemRef}
          bookmarks={bookmarks}
          onSaveBookmark={saveBookmark}
          onRemoveBookmark={removeBookmark}
        />
      </div>
      <button className="jump-bottom" title="Jump to latest" onClick={jumpToBottom}>
        ↓
      </button>
    </div>
  );

  const toolLog = (
    <div className="panel panel-toollog">
      <div className="panel-label panel-tabs">
        <button
          className={`panel-tab${panelTab === "timeline" ? " active" : ""}`}
          onClick={() => setPanelTab("timeline")}
        >
          Timeline · {events.length}
        </button>
        <button
          className={`panel-tab${panelTab === "files" ? " active" : ""}`}
          onClick={() => setPanelTab("files")}
        >
          Files · {files.length}
        </button>
        {errorCount > 0 && (
          <button
            className="panel-tab err-chip"
            title="Show errors in the timeline"
            onClick={() => {
              setPanelTab("timeline");
              setErrSignal((n) => n + 1);
            }}
          >
            ⚠ {errorCount}
          </button>
        )}
      </div>
      <div className="panel-scroll" ref={logScrollRef}>
        {panelTab === "timeline" ? (
          <EventTimeline
            events={events}
            selectedToolId={selectedToolId}
            onSelect={onSelectEvent}
            errorsOnlySignal={errSignal}
          />
        ) : (
          <FileChanges
            files={files}
            selectedToolId={selectedToolId}
            onSelectOp={(id) => selectTool(id, "log")}
          />
        )}
      </div>
    </div>
  );

  const detail = (
    <div className="panel panel-detail">
      <div className="panel-label">Detail</div>
      <div className="panel-scroll">
        {selectedTool && sessionId ? (
          <ToolDetail
            tool={selectedTool}
            sessionId={sessionId}
            onOpenSubagent={openSubagent}
          />
        ) : selectedEvent ? (
          <EventDetail event={selectedEvent} />
        ) : (
          <div className="empty">Select any timeline entry to inspect it.</div>
        )}
      </div>
    </div>
  );

  return (
    <div className="viewer">
      <ViewerHeader
        current={current}
        crumbs={crumbs}
        onNavigate={navigateTo}
        layout={layout}
        onLayoutChange={setLayout}
        live={live}
        subagents={subagentTree}
        subagentCount={subagentCount}
        activePath={agentStack}
        onOpenSubagentPath={setAgentPath}
        onRename={renameSession}
        lineage={lineage}
        onJumpToCompaction={(uuid) => {
          convItemRefs.current
            .get(uuid)
            ?.scrollIntoView({ block: "center", behavior: "smooth" });
        }}
      />

      {sessionId && agentStack.length === 0 && (
        <>
          <SessionBacklinks
            sessionId={sessionId}
            onFocus={(uuid) => {
              if (toolsById.has(uuid)) selectTool(uuid, "log");
              else
                convItemRefs.current
                  .get(uuid)
                  ?.scrollIntoView({ block: "center", behavior: "smooth" });
            }}
          />
          <NotesPanel
            sessionId={sessionId}
            onFocus={(uuid) => {
              if (toolsById.has(uuid)) selectTool(uuid, "log");
              else
                convItemRefs.current
                  .get(uuid)
                  ?.scrollIntoView({ block: "center", behavior: "smooth" });
            }}
          />
          <RelatedSessions sessionId={sessionId} />
          <HealthBar
            sessionId={sessionId}
            onFocus={(uuid) => {
              if (toolsById.has(uuid)) selectTool(uuid, "log");
              else
                convItemRefs.current
                  .get(uuid)
                  ?.scrollIntoView({ block: "center", behavior: "smooth" });
            }}
          />
          <ReentryBanner
            sessionId={sessionId}
            provider={current.provider}
            cwd={current.project_cwd}
            onFocus={(uuid) => {
              if (toolsById.has(uuid)) selectTool(uuid, "log");
              else
                convItemRefs.current
                  .get(uuid)
                  ?.scrollIntoView({ block: "center", behavior: "smooth" });
            }}
          />
        </>
      )}

      <div className="panels">
        {layout === 1 && conversation}
        {layout === 2 && (
          <ResizableSplit direction="row" storageKey="muse.split.row2">
            {conversation}
            <ResizableSplit direction="col" storageKey="muse.split.col2">
              {toolLog}
              {detail}
            </ResizableSplit>
          </ResizableSplit>
        )}
        {layout === 3 && (
          <ResizableSplit direction="row" storageKey="muse.split.row3">
            {conversation}
            {toolLog}
            {detail}
          </ResizableSplit>
        )}
      </div>

      {layout === 1 && overlayOpen && selectedTool && sessionId && (
        <ToolDetailPanel
          tool={selectedTool}
          sessionId={sessionId}
          onClose={() => setOverlayOpen(false)}
          onOpenSubagent={openSubagent}
        />
      )}

      {layout === 1 && overlayOpen && !selectedTool && selectedEvent && (
        <>
          <div className="detail-overlay" onClick={() => setOverlayOpen(false)} />
          <aside className="detail-panel">
            <div className="detail-head">
              <span className="tool-name">{selectedEvent.label || selectedEvent.kind}</span>
              <button className="close-btn" onClick={() => setOverlayOpen(false)}>
                Close ✕
              </button>
            </div>
            <div className="detail-body">
              <EventDetail event={selectedEvent} />
            </div>
          </aside>
        </>
      )}

      {showHelp && (
        <>
          <div className="detail-overlay" onClick={() => setShowHelp(false)} />
          <div className="help-card">
            <div className="help-title">Keyboard shortcuts</div>
            <ul className="help-list">
              <li><kbd>j</kbd>/<kbd>k</kbd> move in timeline</li>
              <li><kbd>Enter</kbd> open the selected event</li>
              <li><kbd>e</kbd> jump to next error</li>
              <li><kbd>/</kbd> focus conversation search</li>
              <li><kbd>n</kbd>/<kbd>N</kbd> next/prev search match (in the search box)</li>
              <li><kbd>?</kbd> toggle this help · <kbd>Esc</kbd> close</li>
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function firstModel(thread: Thread): string | null {
  for (const item of thread.items) if (item.model) return item.model;
  return null;
}

function patchResult(thread: Thread, result: ToolResult): Thread {
  let changed = false;
  const items = thread.items.map((item) => {
    const blocks = item.blocks.map((b) => {
      if (b.tool_use && b.tool_use.id === result.tool_use_id && !b.tool_use.result) {
        changed = true;
        return { ...b, tool_use: { ...b.tool_use, result } };
      }
      return b;
    });
    return changed ? { ...item, blocks } : item;
  });
  return changed ? { ...thread, items } : thread;
}
