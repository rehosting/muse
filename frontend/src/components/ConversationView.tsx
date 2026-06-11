import { useEffect, useMemo, useRef, useState } from "react";
import type { ThreadItem, ToolUse } from "../api/types";
import { summarize } from "./renderers";
import { toolStatus } from "../util/toolIndex";
import Markdown from "./Markdown";
import BookmarkControl from "./BookmarkControl";
import WelcomeBanner from "./WelcomeBanner";

export type SelectSource = "conversation" | "log" | "detail";

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
}

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

/** Renders a thread to look and feel like the Claude Code terminal output. */
export default function ConversationView({
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
}: Props) {
  const [query, setQuery] = useState("");
  const [current, setCurrent] = useState(0);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());

  const q = query.trim().toLowerCase();
  const haystacks = useMemo(() => items.map((it) => itemText(it)), [items]);
  const matches = useMemo(() => {
    if (!q) return [] as string[];
    return items.filter((_, i) => haystacks[i].includes(q)).map((it) => it.uuid);
  }, [items, haystacks, q]);

  useEffect(() => {
    setCurrent(0);
  }, [q]);

  useEffect(() => {
    if (!matches.length) return;
    const uuid = matches[Math.min(current, matches.length - 1)];
    itemRefs.current.get(uuid)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [current, matches]);

  const step = (dir: number) => {
    if (!matches.length) return;
    setCurrent((c) => (c + dir + matches.length) % matches.length);
  };

  const matchSet = new Set(matches);
  const currentUuid = matches.length ? matches[Math.min(current, matches.length - 1)] : null;

  return (
    <div className={`cc${compact ? " cc-compact" : ""}`}>
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

      {items.map((item) => (
        <div
          key={item.uuid}
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
      ))}
    </div>
  );
}

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
