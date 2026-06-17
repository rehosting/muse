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
import { toolStatus } from "../util/toolIndex";
import Markdown from "./Markdown";
import BookmarkControl from "./BookmarkControl";
import WelcomeBanner from "./WelcomeBanner";

export type SelectSource = "conversation" | "log" | "detail";

/** Imperative scroll API the viewer drives (deep links, timeline, search, live
 * append). With virtualization the target item may not be mounted yet, so these
 * expand the render window to include it, then scroll the real element. */
export interface ConversationHandle {
  scrollToUuid: (uuid: string, block?: ScrollLogicalPosition) => void;
  scrollToTool: (toolId: string, block?: ScrollLogicalPosition) => void;
  scrollToBottom: () => void;
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
  registerToolRef: (id: string, el: HTMLElement | null) => void;
  /** Register each message wrapper by uuid so the timeline can scroll to it. */
  registerItemRef?: (uuid: string, el: HTMLElement | null) => void;
  bookmarks: Record<string, string>;
  onSaveBookmark: (messageUuid: string, note: string) => void;
  onRemoveBookmark: (messageUuid: string) => void;
  /** Compact mode (follow panes): no search bar, banner, or bookmark controls. */
  compact?: boolean;
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
}

// Below this many items, virtualization is pure overhead — render everything.
const VIRT_THRESHOLD = 200;
// Render this many px above and below the viewport so items are measured (and
// scrolling never reveals a blank gap) before they reach the edge.
const OVERSCAN = 1500;
// Height assumed for a not-yet-measured row (only affects the scrollbar estimate).
const EST_HEIGHT = 72;

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
    registerToolRef,
    registerItemRef,
    bookmarks,
    onSaveBookmark,
    onRemoveBookmark,
    compact = false,
    highlightUuid = null,
    virtualize = false,
    scrollParentRef,
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
  const [range, setRange] = useState({ start: 0, end: Math.min(N, 60) });
  // Bump to force a re-render after measuring changes the spacer heights.
  const [, setTick] = useState(0);

  // Reset/resize the height cache when the thread changes. Transcripts only
  // APPEND, so on growth we keep prior measurements (indices are stable); a new
  // thread (different first item) starts fresh.
  if (firstUuid.current !== (items[0]?.uuid ?? null)) {
    firstUuid.current = items[0]?.uuid ?? null;
    heights.current = new Array(N).fill(EST_HEIGHT);
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
  }, [active, findScroller, listTop, N]);

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
    let changed = false;
    for (const el of Array.from(list.querySelectorAll<HTMLElement>("[data-vindex]"))) {
      const i = Number(el.dataset.vindex);
      const h = el.offsetHeight;
      if (h && Math.abs((heights.current[i] || EST_HEIGHT) - h) > 1) {
        heights.current[i] = h;
        changed = true;
      }
    }
    if (changed) {
      rebuildCum();
      setTick((t) => t + 1); // re-render spacers; layout effect re-runs but converges
    }
  });

  // New thread (e.g. drilling into a subagent): reset the window to the top, the
  // same as the non-virtualized viewer opened. Appends don't change items[0].
  const threadKey = items[0]?.uuid ?? "";
  useEffect(() => {
    if (!active) return;
    setRange({ start: 0, end: Math.min(N, 60) });
    const sc = findScroller();
    if (sc) sc.scrollTop = 0;
    requestAnimationFrame(recalc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadKey]);

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
        registerToolRef={registerToolRef}
      />
    </div>
  );

  let body: React.ReactNode;
  if (!active) {
    body = items.map((item, i) => renderItem(item, i));
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
    <div className={`cc${compact ? " cc-compact" : ""}`} ref={rootRef}>
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
  registerToolRef: (id: string, el: HTMLElement | null) => void;
}

function ConversationItem({
  item,
  selectedToolId,
  onSelectTool,
  registerToolRef,
}: ItemProps) {
  // User lines that only carried tool_results render nothing here — those
  // results are shown under their tool call, exactly like the CLI.
  if (item.role === "user") {
    if (!item.text) return null;
    return (
      <div className="cc-user">
        <span className="cc-prompt">{">"}</span>
        <div className="cc-md md-tight">
          <Markdown>{item.text}</Markdown>
        </div>
      </div>
    );
  }

  if (item.role === "system") {
    if (!item.text) return null;
    return <div className="cc-system">{item.text}</div>;
  }

  // assistant
  return (
    <div className="cc-assistant">
      {item.blocks.map((b, i) => {
        if (b.kind === "text" && b.text) {
          return (
            <div className="cc-line cc-text-line" key={i}>
              <span className="cc-bullet">⏺</span>
              <div className="cc-md">
                <Markdown>{b.text}</Markdown>
              </div>
            </div>
          );
        }
        if (b.kind === "thinking" && b.text) {
          return <Thinking key={i} text={b.text} />;
        }
        if (b.kind === "tool_use" && b.tool_use) {
          return (
            <ToolLine
              key={i}
              tool={b.tool_use}
              selected={selectedToolId === b.tool_use.id}
              onSelect={() => onSelectTool(b.tool_use!.id, "conversation")}
              registerRef={registerToolRef}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

function ToolLine({
  tool,
  selected,
  onSelect,
  registerRef,
}: {
  tool: ToolUse;
  selected: boolean;
  onSelect: () => void;
  registerRef: (id: string, el: HTMLElement | null) => void;
}) {
  const status = toolStatus(tool);
  const arg = summarize(tool.name, tool.input);
  const resultText = tool.result?.content ?? tool.result?.preview ?? "";

  return (
    <div
      ref={(el) => registerRef(tool.id, el)}
      className={`cc-line cc-tool-line${selected ? " selected" : ""}`}
      onClick={onSelect}
    >
      <div>
        <span className={`cc-bullet status-${status}`}>⏺</span>
        <span className="cc-tool-name">{tool.name}</span>
        <span className="cc-tool-arg">({arg})</span>
        {tool.subagent && <span className="subagent-pill">{tool.subagent.agent_type}</span>}
      </div>
      <ResultConnector text={resultText} truncated={tool.result?.truncated} pending={!tool.result} />
    </div>
  );
}

function ResultConnector({
  text,
  truncated,
  pending,
}: {
  text: string;
  truncated?: boolean;
  pending?: boolean;
}) {
  if (pending) {
    return (
      <div className="cc-result">
        <span className="cc-connector">⎿</span> <span className="cc-dim">running…</span>
      </div>
    );
  }
  const lines = text.split("\n");
  const shown = lines.slice(0, 4);
  const extra = lines.length - shown.length;
  return (
    <div className="cc-result">
      <span className="cc-connector">⎿</span>{" "}
      <span className="cc-result-text">
        {shown.join("\n") || "(No content)"}
        {extra > 0 && <span className="cc-dim">{`\n… +${extra} lines (click to expand)`}</span>}
        {truncated && <span className="cc-dim">{`\n… (output truncated — click to expand)`}</span>}
      </span>
    </div>
  );
}

function Thinking({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="cc-thinking">
      <span className="thinking-star" onClick={() => setOpen(!open)}>
        ✻
      </span>
      <span className="cc-thinking-label" onClick={() => setOpen(!open)}>
        {open ? "Thinking…" : "Thinking… (click to expand)"}
      </span>
      {open && <div className="cc-thinking-body">{text}</div>}
    </div>
  );
}
