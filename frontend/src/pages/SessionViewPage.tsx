import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  FileChange,
  SessionCommit,
  SessionEvent,
  SessionLineage,
  SubagentRef,
  Thread,
  ThreadItem,
  ToolResult,
} from "../api/types";
import CommitsPanel from "../components/CommitsPanel";
import type { Crumb } from "../components/Breadcrumb";
import ConversationView, {
  type ConversationHandle,
  type SelectSource,
  type MessageKind,
} from "../components/ConversationView";
import EventTimeline from "../components/EventTimeline";
import EventDetail from "../components/EventDetail";
import FileChanges from "../components/FileChanges";
import ToolDetail from "../components/ToolDetail";
import ToolDetailPanel from "../components/ToolDetailPanel";
import ViewerHeader, { type LayoutMode } from "../components/ViewerHeader";
import SessionBacklinks from "../components/SessionBacklinks";
import ReentryBanner from "../components/ReentryBanner";
import { type SubNode } from "../components/SubagentTree";
import ResizableSplit from "../components/ResizableSplit";
import { useSessionStream } from "../hooks/useSessionStream";
import { toolMap } from "../util/toolIndex";
import { sessionStats } from "../util/stats";

// How many thread items to fetch per window. Tuned so a window is a small payload
// (a few hundred KB before gzip) while rarely needing a second fetch on open.
const WINDOW = 400;

/** Whether the loaded window reaches the live end of the thread (so live appends
 * belong here, and "jump to latest" is already showing it). */
function atTail(t: Thread): boolean {
  if (t.total_items == null || t.window_start == null) return true; // full thread
  return t.window_start + t.items.length >= t.total_items;
}
function hasEarlier(t: Thread | null): boolean {
  return !!t && t.window_start != null && t.window_start > 0;
}
function hasLater(t: Thread | null): boolean {
  return !!t && !atTail(t);
}

export default function SessionViewPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const agentStack = useMemo(() => {
    const raw = searchParams.get("agent");
    return raw ? raw.split(",").filter(Boolean) : [];
  }, [searchParams]);

  // Phones default to layout 1 (conversation only) — the 3-pane tool log is
  // unusable on a narrow screen. An explicit ?view= always wins.
  const _defaultLayout =
    typeof window !== "undefined" && window.matchMedia("(max-width: 700px)").matches
      ? 1
      : 3;
  const layout = (Number(searchParams.get("view")) || _defaultLayout) as LayoutMode;

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
  // Imperative scroll into the (virtualized) conversation — the target item may
  // not be mounted, so these expand the window then scroll the real element.
  const convViewRef = useRef<ConversationHandle>(null);

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
  const [panelTab, setPanelTab] = useState<"timeline" | "files" | "commits">("timeline");
  const [commits, setCommits] = useState<SessionCommit[]>([]);
  const [errSignal, setErrSignal] = useState(0);

  // Subagent spawns for the WHOLE session (window-independent) — the main thread
  // is loaded a window at a time, so the subagent menu can't be derived from it.
  const [rootSubagents, setRootSubagents] = useState<SubagentRef[]>([]);
  // Guards against overlapping window fetches (scroll can fire many times).
  const windowLoading = useRef(false);

  // ---- data loading ----
  useEffect(() => {
    if (!sessionId) return;
    setMain(null);
    setSelectedToolId(null);
    setLineage(null);
    setRootSubagents([]);
    // Windowed first paint: the server picks head (finished) or tail (live).
    api.getThread(sessionId, { limit: WINDOW }).then(setMain).catch((e) => setError(String(e)));
    api
      .getEvents(sessionId)
      .then((evs) =>
        setRootSubagents(
          evs.flatMap((e) => (e.kind === "subagent" && e.subagent ? [e.subagent] : [])),
        ),
      )
      .catch(() => setRootSubagents([]));
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
    // Provenance is session-level (not per-subagent).
    if (!agentId) {
      api.getSessionCommits(sessionId).then(setCommits).catch(() => setCommits([]));
    }
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
        // The window is showing earlier history (not the live tail): don't graft
        // live items onto a non-contiguous window — just record that more exists
        // so "jump to latest" re-fetches the tail. Otherwise append + autoscroll.
        const bumped =
          prev.total_items != null ? prev.total_items + fresh.length : prev.total_items;
        if (!atTail(prev)) return { ...prev, total_items: bumped };
        setScrollNonce((n) => n + 1); // request auto-scroll to bottom
        return { ...prev, items: [...prev.items, ...fresh], total_items: bumped };
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
    convViewRef.current?.scrollToBottom();
    requestAnimationFrame(() => {
      const el = logScrollRef.current;
      el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
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

  // ---- ranged window loading (main thread only; subagents load whole) ----
  // Fetch the window of older items and PREPEND it (contiguous: `before` ends
  // exactly at our current first index, so no overlap).
  const loadEarlier = useCallback(() => {
    if (!sessionId || windowLoading.current) return;
    setMain((prev) => {
      if (!prev || !hasEarlier(prev)) return prev;
      windowLoading.current = true;
      api
        .getThread(sessionId, { limit: WINDOW, before: prev.window_start ?? 0 })
        .then((w) =>
          setMain((cur) =>
            cur
              ? { ...cur, items: [...w.items, ...cur.items], window_start: w.window_start }
              : cur,
          ),
        )
        .catch(() => {})
        .finally(() => (windowLoading.current = false));
      return prev;
    });
  }, [sessionId]);

  const loadLater = useCallback(() => {
    if (!sessionId || windowLoading.current) return;
    setMain((prev) => {
      if (!prev || !hasLater(prev)) return prev;
      windowLoading.current = true;
      const after = (prev.window_start ?? 0) + prev.items.length;
      api
        .getThread(sessionId, { limit: WINDOW, after })
        .then((w) =>
          setMain((cur) => {
            if (!cur) return cur;
            const seen = new Set(cur.items.map((i) => i.uuid));
            const fresh = w.items.filter((i) => !seen.has(i.uuid));
            return { ...cur, items: [...cur.items, ...fresh], total_items: w.total_items };
          }),
        )
        .catch(() => {})
        .finally(() => (windowLoading.current = false));
      return prev;
    });
  }, [sessionId]);

  // Ensure the window contains `id` (a message uuid or tool_use_id) before a jump.
  // Resolves true once present (fetching an around-window if needed), false if the
  // id exists nowhere. Subagent threads load whole, so this only fetches for main.
  const ensureLoaded = useCallback(
    async (id: string): Promise<boolean> => {
      if (!sessionId || agentStack.length > 0) return true;
      const present = (t: Thread | null) =>
        !!t &&
        t.items.some(
          (it) => it.uuid === id || it.blocks.some((b) => b.tool_use?.id === id),
        );
      if (present(main)) return true;
      try {
        const w = await api.getThread(sessionId, { limit: WINDOW, around: id });
        setMain(w);
        return present(w);
      } catch {
        return false;
      }
    },
    [sessionId, agentStack.length, main],
  );

  // ---- current thread (main or deepest subagent) ----
  const current: Thread | null =
    agentStack.length > 0 ? subThreads[agentStack[agentStack.length - 1]] ?? null : main;

  const toolsById = useMemo(() => (current ? toolMap(current.items) : new Map()), [current]);
  // Latest toolsById for callbacks that run after an async window swap (a rAF
  // closure would otherwise capture a stale map).
  const toolsByIdRef = useRef(toolsById);
  toolsByIdRef.current = toolsById;
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

  // Conversation → Detail pane for non-tool lines: clicking assistant text,
  // thinking, a user message, or a system line opens the matching timeline event.
  // One message uuid can spawn several events, so prefer the one whose kind
  // matches the clicked line, then fall back to any event for that uuid.
  const selectMessage = useCallback(
    (uuid: string, kind: MessageKind) => {
      const ev =
        events.find((e) => e.anchor_uuid === uuid && e.kind === kind) ??
        events.find((e) => e.anchor_uuid === uuid);
      if (!ev) return;
      setSelectedToolId(null);
      setSelectedEvent(ev);
      if (layout === 1) setOverlayOpen(true);
    },
    [events, layout],
  );

  // Scroll the conversation to a message (or its tool) from an external panel
  // (backlinks, notes, health, re-entry, timeline). A tool uuid routes through
  // selection so the detail pane opens too.
  const focusInConversation = useCallback(
    async (uuid: string) => {
      // The target may be outside the loaded window — fetch the window around it
      // first, then scroll once it's rendered.
      const ok = await ensureLoaded(uuid);
      if (!ok) return;
      requestAnimationFrame(() => {
        if (toolsByIdRef.current.has(uuid)) selectTool(uuid, "log");
        else convViewRef.current?.scrollToUuid(uuid, "center");
      });
    },
    [ensureLoaded, selectTool],
  );

  // Deep link: ?focus=<message uuid | tool_use_id> selects + scrolls on load.
  const focus = searchParams.get("focus");
  const focusedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || !current || events.length === 0) return;
    if (focusedRef.current === focus) return;
    focusedRef.current = focus;
    requestAnimationFrame(() => focusInConversation(focus));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, current, events.length]);

  // Global shortcuts: "/" focuses conversation search, "?" toggles help.
  const [showHelp, setShowHelp] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA");
      if (e.key === "Escape") {
        setShowHelp(false);
        setOverlayOpen(false);
        return;
      }
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
      convViewRef.current?.scrollToTool(selectedToolId, "center");
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
    // Nested levels come from already-loaded subagent threads (they load whole).
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
    // Top level comes from the full event timeline, NOT main.items — the main
    // thread is windowed, so its items can't enumerate every subagent spawn.
    return rootSubagents.map((sa) => ({
      agentId: sa.agent_id,
      agentType: sa.agent_type,
      description: sa.description,
      path: [sa.agent_id],
      children: build(subThreads[sa.agent_id], [sa.agent_id]),
    }));
  }, [rootSubagents, subThreads]);

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
    async (ev: SessionEvent) => {
      if (ev.kind === "subagent" && ev.subagent) {
        openSubagent(ev.subagent.agent_id);
        return;
      }
      // The timeline is complete even when the conversation window isn't — make
      // sure the target is loaded before selecting/scrolling to it.
      if (ev.tool_use_id) {
        const ok = await ensureLoaded(ev.tool_use_id);
        if (ok && toolsByIdRef.current.has(ev.tool_use_id)) {
          selectTool(ev.tool_use_id, "log");
          return;
        }
      }
      setSelectedToolId(null);
      setSelectedEvent(ev);
      if (layout === 1) setOverlayOpen(true);
      if (ev.anchor_uuid) {
        const ok = await ensureLoaded(ev.anchor_uuid);
        if (ok) requestAnimationFrame(() => convViewRef.current?.scrollToUuid(ev.anchor_uuid!, "center"));
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectTool, ensureLoaded, layout],
  );

  if (error) return <div className="error-banner">{error}</div>;
  if (!main || !current) return <div className="empty">Loading session…</div>;

  const jumpToBottom = async () => {
    // If we're showing earlier history, the tail isn't loaded — fetch it first.
    if (sessionId && agentStack.length === 0 && main && !atTail(main)) {
      try {
        const t = await api.getThread(sessionId, { limit: WINDOW, anchor: "tail" });
        setMain(t);
        requestAnimationFrame(() => convViewRef.current?.scrollToBottom());
        return;
      } catch {
        /* fall through to a plain scroll */
      }
    }
    convViewRef.current?.scrollToBottom();
  };
  const jumpToTop = async () => {
    // If we're showing later history, the head isn't loaded — fetch it first.
    if (sessionId && agentStack.length === 0 && main && hasEarlier(main)) {
      try {
        const t = await api.getThread(sessionId, { limit: WINDOW, anchor: "head" });
        setMain(t);
        requestAnimationFrame(() => convViewRef.current?.scrollToTop());
        return;
      } catch {
        /* fall through to a plain scroll */
      }
    }
    convViewRef.current?.scrollToTop();
  };
  const conversation = (
    <div className="panel panel-conversation">
      <div className="panel-label">Conversation</div>
      <div className="panel-scroll" ref={convScrollRef}>
        <ConversationView
          ref={convViewRef}
          virtualize
          scrollParentRef={convScrollRef}
          items={current.items}
          cwd={current.project_cwd}
          model={current.model ?? firstModel(current)}
          version={current.version}
          contextWindow={current.context_window ?? ctxWindow}
          provider={current.provider}
          selectedToolId={selectedToolId}
          onSelectTool={selectTool}
          onSelectMessage={selectMessage}
          registerToolRef={registerConvRef}
          registerItemRef={registerConvItemRef}
          bookmarks={bookmarks}
          onSaveBookmark={saveBookmark}
          onRemoveBookmark={removeBookmark}
          hasEarlier={agentStack.length === 0 && hasEarlier(main)}
          hasLater={agentStack.length === 0 && hasLater(main)}
          onNeedEarlier={loadEarlier}
          onNeedLater={loadLater}
          landing={
            agentStack.length === 0 && main && atTail(main) && hasEarlier(main)
              ? "bottom"
              : "top"
          }
        />
      </div>
      <button className="jump-top" title="Jump to top" onClick={jumpToTop}>
        ↑
      </button>
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
        {commits.length > 0 && agentStack.length === 0 && (
          <button
            className={`panel-tab${panelTab === "commits" ? " active" : ""}`}
            title="Git commits this session likely produced (evidence-based)"
            onClick={() => setPanelTab("commits")}
          >
            ⎘ Commits · {commits.length}
          </button>
        )}
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
        ) : panelTab === "files" ? (
          <FileChanges
            files={files}
            selectedToolId={selectedToolId}
            onSelectOp={(id) => focusInConversation(id)}
          />
        ) : (
          <CommitsPanel commits={commits} projectCwd={current?.project_cwd ?? null} />
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
        onJumpToCompaction={(uuid) => focusInConversation(uuid)}
        onFocus={focusInConversation}
      />

      {sessionId && agentStack.length === 0 && (
        <>
          <SessionBacklinks sessionId={sessionId} onFocus={focusInConversation} />
          <ReentryBanner
            sessionId={sessionId}
            provider={current.provider}
            cwd={current.project_cwd}
            onFocus={focusInConversation}
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
          <div
            className="detail-overlay detail-overlay-passthrough"
            onClick={() => setOverlayOpen(false)}
          />
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
