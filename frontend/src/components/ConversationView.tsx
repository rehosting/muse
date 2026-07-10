import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ThreadItem, ToolUse } from "../api/types";
import { summarize } from "./renderers";
import { classifyUser, toolArg, toolBody, toolTitle } from "./ccInline";
import { toolStatus } from "../util/toolIndex";
import { computeToolRuns, type ToolRun } from "../util/toolRuns";
import Markdown from "./Markdown";
import BookmarkControl from "./BookmarkControl";
import WelcomeBanner from "./WelcomeBanner";

export type SelectSource = "conversation" | "log" | "detail";

/** Kinds of conversation entry that map to a timeline event (non-tool). */
export type MessageKind = "assistant_text" | "thinking" | "user" | "system";

/** Imperative scroll API the viewer drives (deep links, timeline, search, live
 * append). With virtualization the target item may not be mounted yet, so these
 * expand the render window to include it, then scroll the real element. */
export interface ConversationHandle {
  scrollToUuid: (uuid: string, block?: ScrollLogicalPosition) => void;
  scrollToTool: (toolId: string, block?: ScrollLogicalPosition) => void;
  scrollToBottom: () => void;
  scrollToTop: () => void;
}

interface Props {
  items: ThreadItem[];
  cwd: string | null;
  model: string | null;
  version?: string | null;
  contextWindow?: number;
  provider?: string;
  selectedToolId: string | null;
  onSelectTool: (id: string, source: SelectSource) => void;
  /** Click any non-tool line (assistant text, thinking, user, system) to open the
   * matching timeline entry in the Detail pane. Resolved by (uuid, kind). */
  onSelectMessage?: (uuid: string, kind: MessageKind) => void;
  registerToolRef: (id: string, el: HTMLElement | null) => void;
  /** Register each message wrapper by uuid so the timeline can scroll to it. */
  registerItemRef?: (uuid: string, el: HTMLElement | null) => void;
  bookmarks: Record<string, string>;
  onSaveBookmark: (messageUuid: string, note: string) => void;
  onRemoveBookmark: (messageUuid: string) => void;
  /** Compact mode (follow panes): no search bar, banner, or bookmark controls. */
  compact?: boolean;
  /** Focus mode (phone cockpit): collapse thinking + tool output to one line each
   * so the actual conversation stays readable. Tap to expand any of them. */
  focus?: boolean;
  /** Persistently highlight the message with this uuid (e.g. the step an
   * investigation reference points at). */
  highlightUuid?: string | null;
  /** Render only the messages near the viewport. Only the main viewer turns this
   * on (it owns the scroll container + the imperative handle); the live panes
   * keep rendering everything so their own scroll logic is untouched. */
  virtualize?: boolean;
  /** The scroll container that wraps this view (the viewer's `.panel-scroll`).
   * Required for virtualize to anchor correctly; falls back to walking the DOM. */
  scrollParentRef?: React.RefObject<HTMLElement>;
  /** Ranged loading: items is a contiguous window of a larger thread. When the
   * user scrolls within EDGE rows of an unloaded edge, the viewer fetches the
   * adjacent window. The parent guards against overlapping fetches. */
  hasEarlier?: boolean;
  hasLater?: boolean;
  onNeedEarlier?: () => void;
  onNeedLater?: () => void;
  /** Where a freshly-loaded thread lands. "bottom" for a live session opened on
   * its tail window (show the latest); "top" otherwise (read from the start). */
  landing?: "top" | "bottom";
}

// Fire a load when the rendered window comes within this many rows of an edge.
const EDGE = 8;

// Below this many items, virtualization is pure overhead — render everything.
const VIRT_THRESHOLD = 200;
// Render this many px above and below the viewport so items are measured (and
// scrolling never reveals a blank gap) before they reach the edge.
const OVERSCAN = 1500;
// Height assumed for a not-yet-measured row (only affects the scrollbar estimate).
const EST_HEIGHT = 72;

// Shared empty map so non-focus renders never allocate (or regroup).
const EMPTY_RUNS = new Map<string, ToolRun>();

/** All searchable text for an item (assistant/user text, tool args + results). */
function itemText(item: ThreadItem): string {
  const parts: string[] = [];
  if (item.text) parts.push(item.text);
  for (const b of item.blocks) {
    if (b.text) parts.push(b.text);
    if (b.tool_use) {
      parts.push(b.tool_use.name, summarize(b.tool_use.name, b.tool_use.input));
      const r = b.tool_use.result;
      if (r?.content) parts.push(r.content);
      if (r?.preview) parts.push(r.preview);
    }
  }
  return parts.join("\n").toLowerCase();
}

/** Last index ≤ target in a sorted cumulative array (binary search). */
function lastAtMost(cum: number[], target: number): number {
  let lo = 0;
  let hi = cum.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (cum[mid] <= target) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Renders a thread to look and feel like the Claude Code terminal output. */
function ConversationView(
  {
    items,
    cwd,
    model,
    version,
    contextWindow,
    provider = "claude",
    selectedToolId,
    onSelectTool,
    onSelectMessage,
    registerToolRef,
    registerItemRef,
    bookmarks,
    onSaveBookmark,
    onRemoveBookmark,
    compact = false,
    focus = false,
    highlightUuid = null,
    virtualize = false,
    scrollParentRef,
    hasEarlier = false,
    hasLater = false,
    onNeedEarlier,
    onNeedLater,
    landing = "top",
  }: Props,
  ref: React.Ref<ConversationHandle>,
) {
  const [query, setQuery] = useState("");
  const [current, setCurrent] = useState(0);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());

  const q = query.trim().toLowerCase();
  const haystacks = useMemo(() => items.map((it) => itemText(it)), [items]);
  const matches = useMemo(() => {
    if (!q) return [] as string[];
    return items.filter((_, i) => haystacks[i].includes(q)).map((it) => it.uuid);
  }, [items, haystacks, q]);

  // uuid / tool-id -> item index, for the imperative scroll-to methods.
  const indexByUuid = useMemo(() => {
    const m = new Map<string, number>();
    items.forEach((it, i) => m.set(it.uuid, i));
    return m;
  }, [items]);
  const indexByTool = useMemo(() => {
    const m = new Map<string, number>();
    items.forEach((it, i) => {
      for (const b of it.blocks) if (b.tool_use) m.set(b.tool_use.id, i);
    });
    return m;
  }, [items]);

  // ---- virtualization state ----
  const N = items.length;
  const active = virtualize && N > VIRT_THRESHOLD;
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLElement | null>(null);
  const heights = useRef<number[]>([]);
  const cum = useRef<number[]>([0]);
  const firstUuid = useRef<string | null>(null);
  // Set when a window is PREPENDED (older items loaded above): the layout effect
  // consumes it to shift the render range + scrollTop so the viewport stays put.
  const prependShift = useRef(0);
  const [range, setRange] = useState({ start: 0, end: Math.min(N, 60) });
  // Bump to force a re-render after measuring changes the spacer heights.
  const [, setTick] = useState(0);

  // Reconcile the height cache with the current items. Two growth shapes:
  //  - APPEND (live activity / load-later): items[0] unchanged, indices stable —
  //    keep prior measurements, extend with estimates.
  //  - PREPEND (load-earlier): older items inserted at the front, every index
  //    shifts right by K. Shift the cache to keep measurements aligned and flag
  //    the layout effect to anchor the scroll. A genuinely new thread resets.
  const curFirst = items[0]?.uuid ?? null;
  if (firstUuid.current !== curFirst) {
    const k = firstUuid.current ? items.findIndex((it) => it.uuid === firstUuid.current) : -1;
    if (k > 0) {
      const shifted = new Array(N).fill(EST_HEIGHT);
      for (let i = 0; i < heights.current.length && i + k < N; i++) {
        shifted[i + k] = heights.current[i];
      }
      heights.current = shifted;
      prependShift.current = k;
    } else {
      heights.current = new Array(N).fill(EST_HEIGHT);
    }
    firstUuid.current = curFirst;
  } else if (heights.current.length !== N) {
    const next = new Array(N).fill(EST_HEIGHT);
    for (let i = 0; i < Math.min(N, heights.current.length); i++) next[i] = heights.current[i];
    heights.current = next;
  }

  const rebuildCum = useCallback(() => {
    const h = heights.current;
    const c = new Array(N + 1);
    c[0] = 0;
    for (let i = 0; i < N; i++) c[i + 1] = c[i] + (h[i] || EST_HEIGHT);
    cum.current = c;
  }, [N]);

  const findScroller = useCallback((): HTMLElement | null => {
    if (scrollParentRef?.current) return scrollParentRef.current;
    if (scrollerRef.current) return scrollerRef.current;
    let el: HTMLElement | null = rootRef.current?.parentElement ?? null;
    while (el) {
      const oy = getComputedStyle(el).overflowY;
      if (oy === "auto" || oy === "scroll") break;
      el = el.parentElement;
    }
    scrollerRef.current = el;
    return el;
  }, [scrollParentRef]);

  // Translate the scroller's scrollTop into the list's coordinate space.
  const listTop = useCallback((): number => {
    const sc = findScroller();
    const list = listRef.current;
    if (!sc || !list) return 0;
    return list.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop;
  }, [findScroller]);

  const recalc = useCallback(() => {
    if (!active) return;
    const sc = findScroller();
    if (!sc) return;
    const top = sc.scrollTop - listTop();
    const viewTop = top - OVERSCAN;
    const viewBottom = top + sc.clientHeight + OVERSCAN;
    const c = cum.current;
    const start = lastAtMost(c, Math.max(0, viewTop));
    const end = Math.min(N, lastAtMost(c, viewBottom) + 2);
    setRange((r) => (r.start === start && r.end === end ? r : { start, end }));
    // Near an unloaded edge => ask the parent for the adjacent window (it guards
    // against overlapping fetches, so calling on every scroll frame is safe).
    if (hasEarlier && onNeedEarlier && start <= EDGE) onNeedEarlier();
    if (hasLater && onNeedLater && end >= N - EDGE) onNeedLater();
  }, [active, findScroller, listTop, N, hasEarlier, hasLater, onNeedEarlier, onNeedLater]);

  // Anchor the viewport across a prepend: shift the render range by the K inserted
  // rows and push scrollTop down by their (estimated) height, so the rows the user
  // was looking at stay under their eyes instead of jumping up.
  useLayoutEffect(() => {
    const k = prependShift.current;
    if (!k || !active) return;
    prependShift.current = 0;
    rebuildCum();
    const addedAbove = cum.current[k] ?? 0;
    setRange((r) => ({ start: r.start + k, end: r.end + k }));
    const sc = findScroller();
    if (sc) sc.scrollTop += addedAbove;
  });

  // Recompute the window as the user scrolls (rAF-throttled).
  useEffect(() => {
    if (!active) return;
    const sc = findScroller();
    if (!sc) return;
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        recalc();
      });
    };
    sc.addEventListener("scroll", onScroll, { passive: true });
    rebuildCum();
    recalc();
    return () => {
      sc.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [active, findScroller, recalc, rebuildCum]);

  // Measure rendered rows; once a row's real height is known the spacer estimate
  // is corrected. Measurements persist, so each row only corrects once.
  useLayoutEffect(() => {
    if (!active) return;
    const list = listRef.current;
    if (!list) return;
    const sc = findScroller();
    // Rows sitting ABOVE the viewport top, once measured, would otherwise shift
    // everything below them (and the content under the user's eyes) — compensate
    // scrollTop by their height delta. Matters for prepended load-earlier windows.
    const viewTop = sc ? sc.scrollTop - listTop() : 0;
    const c = cum.current;
    let aboveDelta = 0;
    let changed = false;
    for (const el of Array.from(list.querySelectorAll<HTMLElement>("[data-vindex]"))) {
      const i = Number(el.dataset.vindex);
      const h = el.offsetHeight;
      const old = heights.current[i] || EST_HEIGHT;
      if (h && Math.abs(old - h) > 1) {
        if ((c[i] ?? 0) < viewTop) aboveDelta += h - old;
        heights.current[i] = h;
        changed = true;
      }
    }
    if (changed) {
      rebuildCum();
      if (sc && aboveDelta) sc.scrollTop += aboveDelta;
      setTick((t) => t + 1); // re-render spacers; layout effect re-runs but converges
    }
  });

  // New thread (e.g. drilling into a subagent): reset the window to the top, the
  // same as the non-virtualized viewer opened. NOT on append (items[0] unchanged)
  // nor prepend (old first still present — the prepend effect anchors instead).
  const prevResetFirst = useRef<string | null>(null);
  useEffect(() => {
    if (!active) return;
    const first = items[0]?.uuid ?? null;
    const prev = prevResetFirst.current;
    prevResetFirst.current = first;
    if (prev === first) return;
    if (prev && items.some((it) => it.uuid === prev)) return; // prepend, keep position
    if (landing === "bottom") {
      // Live session opened on its tail window: show the latest activity.
      setRange({ start: Math.max(0, N - 50), end: N });
      requestAnimationFrame(() => {
        const sc = findScroller();
        if (sc) sc.scrollTop = sc.scrollHeight;
        requestAnimationFrame(recalc);
      });
      return;
    }
    setRange({ start: 0, end: Math.min(N, 60) });
    const sc = findScroller();
    if (sc) sc.scrollTop = 0;
    requestAnimationFrame(recalc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  const scrollToIndex = useCallback(
    (i: number, block: ScrollLogicalPosition = "center") => {
      if (i < 0 || i >= N) return;
      if (active) setRange((r) => ({ start: Math.min(r.start, Math.max(0, i - 12)), end: Math.max(r.end, Math.min(N, i + 12)) }));
      requestAnimationFrame(() => {
        const el = listRef.current?.querySelector<HTMLElement>(`[data-vindex="${i}"]`);
        el?.scrollIntoView({ block, behavior: "auto" });
        requestAnimationFrame(recalc);
      });
    },
    [active, N, recalc],
  );

  useImperativeHandle(
    ref,
    (): ConversationHandle => ({
      scrollToUuid: (uuid, block = "center") => {
        const i = indexByUuid.get(uuid);
        if (i !== undefined) scrollToIndex(i, block);
      },
      scrollToTool: (toolId, block = "center") => {
        const i = indexByTool.get(toolId);
        if (i !== undefined) scrollToIndex(i, block);
      },
      scrollToBottom: () => {
        if (active) setRange({ start: Math.max(0, N - 50), end: N });
        requestAnimationFrame(() => {
          const sc = findScroller();
          if (sc) sc.scrollTop = sc.scrollHeight;
          requestAnimationFrame(() => {
            const s = findScroller();
            if (s) s.scrollTop = s.scrollHeight;
            recalc();
          });
        });
      },
      scrollToTop: () => {
        if (active) setRange({ start: 0, end: Math.min(N, 50) });
        requestAnimationFrame(() => {
          const sc = findScroller();
          if (sc) sc.scrollTop = 0;
          requestAnimationFrame(() => {
            const s = findScroller();
            if (s) s.scrollTop = 0;
            recalc();
          });
        });
      },
    }),
    [indexByUuid, indexByTool, scrollToIndex, active, N, findScroller, recalc],
  );

  useEffect(() => {
    setCurrent(0);
  }, [q]);

  useEffect(() => {
    if (!matches.length) return;
    const uuid = matches[Math.min(current, matches.length - 1)];
    const i = indexByUuid.get(uuid);
    if (active && i !== undefined) {
      scrollToIndex(i, "center");
    } else {
      itemRefs.current.get(uuid)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [current, matches, active, indexByUuid, scrollToIndex]);

  const step = (dir: number) => {
    if (!matches.length) return;
    setCurrent((c) => (c + dir + matches.length) % matches.length);
  };

  const matchSet = new Set(matches);
  const currentUuid = matches.length ? matches[Math.min(current, matches.length - 1)] : null;

  // Focus mode: fold runs of consecutive tool-only items into one row each.
  // Memo keyed on items identity — the reader hook only swaps items on real
  // updates, so this doesn't recompute on no-op polls. Open state is keyed by
  // the run's first-member uuid, which appends never change.
  const runs = useMemo(
    () => (focus ? computeToolRuns(items) : EMPTY_RUNS),
    [focus, items],
  );
  const [openRuns, setOpenRuns] = useState<Set<string>>(() => new Set());
  const toggleRun = useCallback((key: string) => {
    setOpenRuns((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const renderItem = (item: ThreadItem, i: number) => (
    <div
      key={item.uuid}
      data-vindex={i}
      ref={(el) => {
        if (el) itemRefs.current.set(item.uuid, el);
        else itemRefs.current.delete(item.uuid);
        registerItemRef?.(item.uuid, el);
      }}
      className={
        (matchSet.has(item.uuid)
          ? `cc-item match${item.uuid === currentUuid ? " match-current" : ""}`
          : "cc-item") + (item.uuid === highlightUuid ? " cc-item-ref" : "")
      }
    >
      {!compact && item.role !== "system" && (
        <BookmarkControl
          note={bookmarks[item.uuid]}
          onSave={(note) => onSaveBookmark(item.uuid, note)}
          onRemove={() => onRemoveBookmark(item.uuid)}
        />
      )}
      <ConversationItem
        item={item}
        selectedToolId={selectedToolId}
        onSelectTool={onSelectTool}
        onSelectMessage={onSelectMessage}
        registerToolRef={registerToolRef}
        focus={focus}
      />
    </div>
  );

  let body: React.ReactNode;
  if (!active) {
    if (runs.size > 0) {
      // Grouped render (focus only): a run's first member renders the fold row;
      // the members render only while the run is open. A collapsed run at the
      // LIVE TAIL still shows its newest call, so a working session's current
      // step stays visible. Search matches force the run open so results
      // aren't invisible. Item keys/refs are untouched — renderItem as-is.
      const lastUuid = items[items.length - 1]?.uuid;
      const nodes: React.ReactNode[] = [];
      items.forEach((item, i) => {
        const run = runs.get(item.uuid);
        if (!run) {
          nodes.push(renderItem(item, i));
          return;
        }
        const open =
          openRuns.has(run.key) || (q !== "" && [...run.memberUuids].some((u) => matchSet.has(u)));
        if (item.uuid === run.firstUuid) {
          nodes.push(
            <ToolRunRow
              key={`run:${run.key}`}
              run={run}
              open={open}
              onToggle={() => toggleRun(run.key)}
            />,
          );
          if (!open && lastUuid !== undefined && run.memberUuids.has(lastUuid) && run.lastTool) {
            nodes.push(
              <div key={`run-live:${run.key}`} className="cc-assistant cc-run-live">
                <ToolLine
                  tool={run.lastTool}
                  selected={selectedToolId === run.lastTool.id}
                  onSelect={() => onSelectTool(run.lastTool!.id, "conversation")}
                  registerRef={registerToolRef}
                  focus={focus}
                />
              </div>,
            );
          }
        }
        if (open) nodes.push(renderItem(item, i));
      });
      body = nodes;
    } else {
      body = items.map((item, i) => renderItem(item, i));
    }
  } else {
    rebuildCum(); // authoritative offsets from the latest measured heights
    const c = cum.current;
    const start = Math.max(0, Math.min(range.start, N));
    const end = Math.max(start, Math.min(range.end, N));
    const top = c[start] ?? 0;
    const bottom = Math.max(0, (c[N] ?? 0) - (c[end] ?? 0));
    body = (
      <>
        <div style={{ height: top }} aria-hidden />
        {items.slice(start, end).map((item, k) => renderItem(item, start + k))}
        <div style={{ height: bottom }} aria-hidden />
      </>
    );
  }

  return (
    <div className={`cc${compact ? " cc-compact" : ""}${focus ? " cc-focus" : ""}`} ref={rootRef}>
      {!compact && (
      <div className="cc-search">
        <input
          className="cc-search-input"
          placeholder="Search conversation…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") step(e.shiftKey ? -1 : 1);
            if (e.key === "Escape") setQuery("");
          }}
        />
        {q && (
          <>
            <span className="cc-search-count">
              {matches.length ? `${current + 1}/${matches.length}` : "0 results"}
            </span>
            <button className="cc-search-btn" onClick={() => step(-1)} disabled={!matches.length}>
              ↑
            </button>
            <button className="cc-search-btn" onClick={() => step(1)} disabled={!matches.length}>
              ↓
            </button>
            <button className="cc-search-btn" onClick={() => setQuery("")}>
              ✕
            </button>
          </>
        )}
      </div>
      )}

      {!compact && (
        <WelcomeBanner
          cwd={cwd}
          model={model}
          version={version}
          contextWindow={contextWindow}
          provider={provider}
        />
      )}

      <div ref={listRef}>{body}</div>
    </div>
  );
}

export default forwardRef(ConversationView);

interface ItemProps {
  item: ThreadItem;
  selectedToolId: string | null;
  onSelectTool: (id: string, source: SelectSource) => void;
  onSelectMessage?: (uuid: string, kind: MessageKind) => void;
  registerToolRef: (id: string, el: HTMLElement | null) => void;
  focus?: boolean;
}

/** Fire `cb` only for a genuine click — not while the user is selecting text and
 * not when they clicked a link inside the rendered markdown. */
function clickToSelect(cb: () => void) {
  return (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("a")) return;
    const sel = window.getSelection();
    if (sel && sel.toString().length > 0) return;
    cb();
  };
}

function ConversationItem({
  item,
  selectedToolId,
  onSelectTool,
  onSelectMessage,
  registerToolRef,
  focus = false,
}: ItemProps) {
  // User lines that only carried tool_results render nothing here — those
  // results are shown under their tool call, exactly like the CLI.
  if (item.role === "user") {
    if (!item.text) return null;
    const u = classifyUser(item.text);
    if (u.kind === "hidden") return null;
    if (u.kind === "command") {
      // Slash command, the way the CLI echoes it back: `> /cmd args`.
      return (
        <div className="cc-user cc-cmd">
          <span className="cc-prompt">{">"}</span>
          <span className="cc-cmd-name">{u.name}</span>
          {u.args && <span className="cc-cmd-args"> {u.args}</span>}
        </div>
      );
    }
    if (u.kind === "stdout") {
      // Local command output — dim, under the command, like the terminal.
      return <div className="cc-cmd-out">{u.text}</div>;
    }
    return (
      <div
        className={`cc-user${onSelectMessage ? " cc-clickable" : ""}`}
        onClick={onSelectMessage && clickToSelect(() => onSelectMessage(item.uuid, "user"))}
      >
        <span className="cc-prompt">{">"}</span>
        <div className="cc-md md-tight">
          <Markdown>{u.text}</Markdown>
        </div>
      </div>
    );
  }

  if (item.role === "system") {
    if (!item.text) return null;
    // Color by the line's level the way the CLI does (error red, etc.).
    const lvl = item.level ?? "info";
    return (
      <div
        className={`cc-system cc-system-${lvl}${onSelectMessage ? " cc-clickable" : ""}`}
        onClick={onSelectMessage && clickToSelect(() => onSelectMessage(item.uuid, "system"))}
      >
        {item.text}
      </div>
    );
  }

  // assistant
  return (
    <div className="cc-assistant">
      {item.blocks.map((b, i) => {
        if (b.kind === "text" && b.text) {
          return (
            <div
              className={`cc-line cc-text-line${onSelectMessage ? " cc-clickable" : ""}`}
              key={i}
              onClick={
                onSelectMessage &&
                clickToSelect(() => onSelectMessage(item.uuid, "assistant_text"))
              }
            >
              <span className="cc-bullet">⏺</span>
              <div className="cc-md">
                <Markdown>{b.text}</Markdown>
              </div>
            </div>
          );
        }
        if (b.kind === "thinking" && b.text) {
          return (
            <Thinking
              key={i}
              text={b.text}
              defaultOpen={!focus}
              onSelect={onSelectMessage && (() => onSelectMessage(item.uuid, "thinking"))}
            />
          );
        }
        if (b.kind === "tool_use" && b.tool_use) {
          return (
            <ToolLine
              key={i}
              tool={b.tool_use}
              selected={selectedToolId === b.tool_use.id}
              onSelect={() => onSelectTool(b.tool_use!.id, "conversation")}
              registerRef={registerToolRef}
              focus={focus}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

// How many distinct tools the collapsed row lists before eliding.
const RUN_COUNTS_SHOWN = 4;

// Max result lines a collapsed tool shows in focus (phone) mode before "show more".
const FOCUS_RESULT_LINES = 3;

/** One tap-to-unfold row standing in for a whole run of tool calls (focus
 * mode). Open, it becomes the fold header above the expanded members. */
function ToolRunRow({
  run,
  open,
  onToggle,
}: {
  run: ToolRun;
  open: boolean;
  onToggle: () => void;
}) {
  const status = run.errors > 0 ? "error" : run.running ? "pending" : "ok";
  const shown = run.counts.slice(0, RUN_COUNTS_SHOWN);
  const elided = run.counts.length - shown.length;
  return (
    <div
      className={`cc-line cc-run-row${open ? " open" : ""}`}
      role="button"
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onToggle();
      }}
    >
      <span className={`cc-bullet status-${status}`}>{open ? "▾" : "⚒"}</span>
      <span className="cc-run-label">
        {run.calls} steps{run.running && !open ? "…" : ""}
      </span>
      <span className="cc-run-counts">
        {shown.map((c) => (c.n > 1 ? `${c.name} ×${c.n}` : c.name)).join("  ") +
          (elided > 0 ? `  +${elided} more` : "")}
      </span>
      {run.errors > 0 && (
        <span className="cc-run-errors">
          {run.errors} error{run.errors === 1 ? "" : "s"}
        </span>
      )}
      {open && <span className="cc-run-fold">tap to fold</span>}
    </div>
  );
}

function ToolLine({
  tool,
  selected,
  onSelect,
  registerRef,
  focus = false,
}: {
  tool: ToolUse;
  selected: boolean;
  onSelect: () => void;
  registerRef: (id: string, el: HTMLElement | null) => void;
  focus?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const status = toolStatus(tool);
  const arg = toolArg(tool);

  // A plan or a question is content to read/answer, not a step to skim past — so
  // render it inline (visible in the reader) instead of a collapsed tool row.
  if (tool.name === "ExitPlanMode") {
    return <PlanLine tool={tool} status={status} registerRef={registerRef} defaultOpen={focus} />;
  }
  if (tool.name === "AskUserQuestion") {
    return <QuestionLine tool={tool} status={status} registerRef={registerRef} />;
  }

  return (
    <div
      ref={(el) => registerRef(tool.id, el)}
      className={`cc-line cc-tool-line${selected ? " selected" : ""}`}
      onClick={() => {
        // Inline expand is the primary affordance; selection keeps the timeline
        // and (if open) the side panel in sync.
        setExpanded((e) => !e);
        onSelect();
      }}
    >
      <div className={`cc-tool-head${expanded ? " open" : ""}`}>
        <span className={`cc-bullet status-${status}`}>⏺</span>
        <span className="cc-tool-name">{toolTitle(tool.name)}</span>
        {arg && <span className="cc-tool-arg">({arg})</span>}
        {tool.subagent && <span className="subagent-pill">{tool.subagent.agent_type}</span>}
      </div>
      <ResultConnector tool={tool} expanded={expanded} focus={focus} />
    </div>
  );
}

/** ExitPlanMode rendered as the plan itself: the markdown body inline (capped
 *  with its own scroll, expandable), so a plan is reviewable in the reader
 *  instead of only in the raw terminal. `answered` (has a result) dims it. */
function PlanLine({
  tool,
  status,
  registerRef,
  defaultOpen = false,
}: {
  tool: ToolUse;
  status: string;
  registerRef: (id: string, el: HTMLElement | null) => void;
  defaultOpen?: boolean;
}) {
  const [full, setFull] = useState(defaultOpen);
  const plan = String(tool.input.plan ?? "").trim();
  const answered = tool.result != null;
  return (
    <div
      ref={(el) => registerRef(tool.id, el)}
      className={`cc-line cc-plan-line${answered ? " answered" : ""}`}
    >
      <div className="cc-tool-head">
        <span className={`cc-bullet status-${status}`}>⏺</span>
        <span className="cc-tool-name">Plan</span>
        {answered && <span className="cc-plan-answered">answered</span>}
      </div>
      {plan ? (
        <>
          <div className={`cc-plan-body${full ? " expanded" : ""}`}>
            <Markdown>{plan}</Markdown>
          </div>
          <button className="cc-plan-toggle" onClick={() => setFull((v) => !v)}>
            {full ? "▴ collapse plan" : "▾ read full plan"}
          </button>
        </>
      ) : (
        <div className="cc-plan-body">
          <em>(empty plan)</em>
        </div>
      )}
    </div>
  );
}

/** AskUserQuestion rendered read-only in the stream: the question and its
 *  choices, so it's visible in the reader. Answering happens through the
 *  OptionPicker (which addresses the live pane); this is the record of the ask. */
function QuestionLine({
  tool,
  status,
  registerRef,
}: {
  tool: ToolUse;
  status: string;
  registerRef: (id: string, el: HTMLElement | null) => void;
}) {
  const questions = Array.isArray(tool.input.questions)
    ? (tool.input.questions as Record<string, unknown>[])
    : [];
  const answered = tool.result != null;
  return (
    <div
      ref={(el) => registerRef(tool.id, el)}
      className={`cc-line cc-question-line${answered ? " answered" : ""}`}
    >
      <div className="cc-tool-head">
        <span className={`cc-bullet status-${status}`}>⏺</span>
        <span className="cc-tool-name">Question</span>
        {answered && <span className="cc-plan-answered">answered</span>}
      </div>
      {questions.map((q, qi) => {
        const opts = Array.isArray(q.options) ? (q.options as Record<string, unknown>[]) : [];
        return (
          <div key={qi} className="cc-question-block">
            <div className="cc-question-prompt">
              {String(q.question ?? q.header ?? "Select an option")}
            </div>
            <ul className="cc-question-opts">
              {opts.map((o, oi) => (
                <li key={oi}>
                  <span className="cc-question-opt-label">{String(o.label ?? "")}</span>
                  {o.description ? (
                    <span className="cc-question-opt-desc"> — {String(o.description)}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function ResultConnector({
  tool,
  expanded,
  focus = false,
}: {
  tool: ToolUse;
  expanded: boolean;
  focus?: boolean;
}) {
  if (!tool.result) {
    return (
      <div className="cc-result">
        <span className="cc-connector">⎿</span>
        <div className="cc-result-body">
          <span className="cc-dim">running…</span>
        </div>
      </div>
    );
  }
  const { rows, limit } = toolBody(tool);
  // Focus mode (phone): show at most a few lines of the result unless expanded, so
  // a long tool output never dominates the small screen. The cap is format-aware —
  // it never exceeds the tool's own preview limit (Read is 1 line, Bash up to 8…),
  // so terse results stay terse and only verbose ones get clamped to FOCUS_RESULT_LINES.
  const clamped = focus && !expanded;
  const effectiveLimit = clamped ? Math.min(limit, FOCUS_RESULT_LINES) : limit;
  const shown = expanded ? rows : rows.slice(0, effectiveLimit);
  const hidden = rows.length - shown.length;
  return (
    <div className={`cc-result${clamped ? " cc-result-clamped" : ""}`}>
      <span className="cc-connector">⎿</span>
      <div className="cc-result-body">
        {shown.map((r, i) => (
          <div key={i} className={r.cls ?? "cc-result-line"}>
            {r.text || " "}
          </div>
        ))}
        {hidden > 0 && (
          <div className="cc-dim">{`… +${hidden} line${hidden === 1 ? "" : "s"} (click to expand)`}</div>
        )}
        {tool.result.truncated && expanded && (
          <div className="cc-dim">… (output truncated — open in side panel for full content)</div>
        )}
      </div>
    </div>
  );
}

function Thinking({
  text,
  onSelect,
  defaultOpen = true,
}: {
  text: string;
  onSelect?: () => void;
  defaultOpen?: boolean;
}) {
  // CC shows reasoning expanded in dim italic by default; collapsible to tidy up.
  // The phone cockpit starts it collapsed (defaultOpen=false) to cut clutter.
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="cc-thinking">
      <span className="thinking-star" onClick={() => setOpen(!open)}>
        ✻
      </span>
      <span className="cc-thinking-label" onClick={() => setOpen(!open)}>
        Thinking…
      </span>
      {open && (
        <div
          className={`cc-thinking-body${onSelect ? " cc-clickable" : ""}`}
          onClick={onSelect && clickToSelect(onSelect)}
        >
          {text}
        </div>
      )}
    </div>
  );
}
