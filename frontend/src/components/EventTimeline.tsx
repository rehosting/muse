import { useEffect, useMemo, useRef, useState } from "react";
import type { EventKind, SessionEvent } from "../api/types";

type Category = "messages" | "tools" | "thinking" | "system" | "lifecycle" | "subagents";

const KIND_CATEGORY: Record<EventKind, Category> = {
  user: "messages",
  assistant_text: "messages",
  tool_call: "tools",
  tool_result: "tools",
  thinking: "thinking",
  system: "system",
  lifecycle: "lifecycle",
  subagent: "subagents",
};

const CATEGORIES: Category[] = ["messages", "tools", "thinking", "subagents", "system", "lifecycle"];

const GLYPH: Record<EventKind, string> = {
  user: "›",
  assistant_text: "⏺",
  thinking: "✻",
  tool_call: "⏵",
  tool_result: "⎿",
  subagent: "⊕",
  system: "ℹ",
  lifecycle: "•",
};

function fmtDur(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60000);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60}m`;
}

interface Props {
  events: SessionEvent[];
  selectedToolId: string | null;
  onSelect: (ev: SessionEvent) => void;
  /** Incrementing this turns on the errors-only filter (from the parent chip). */
  errorsOnlySignal?: number;
}

/** Complete low-level timeline of every session entry, with category filters
 * and keyboard navigation (j/k move, Enter activate, e = next error). */
export default function EventTimeline({
  events,
  selectedToolId,
  onSelect,
  errorsOnlySignal,
}: Props) {
  const [hidden, setHidden] = useState<Set<Category>>(new Set());
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    if (errorsOnlySignal) setErrorsOnly(true);
  }, [errorsOnlySignal]);
  const rowRefs = useRef<Map<number, HTMLElement>>(new Map());

  const filtered = useMemo(
    () =>
      events.filter(
        (e) =>
          !hidden.has(KIND_CATEGORY[e.kind]) && (!errorsOnly || e.is_error),
      ),
    [events, hidden, errorsOnly],
  );

  useEffect(() => {
    if (cursor >= filtered.length) setCursor(Math.max(0, filtered.length - 1));
  }, [filtered.length, cursor]);

  // Conversation → timeline: scroll to the row of the externally-selected tool.
  useEffect(() => {
    if (!selectedToolId) return;
    const i = filtered.findIndex(
      (e) => e.tool_use_id === selectedToolId && e.kind === "tool_call",
    );
    if (i >= 0) rowRefs.current.get(i)?.scrollIntoView({ block: "center" });
  }, [selectedToolId, filtered]);

  // Keyboard nav, scoped to the viewer (ignored while typing in inputs).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        setCursor((c) => {
          const next = e.key === "j" ? Math.min(filtered.length - 1, c + 1) : Math.max(0, c - 1);
          rowRefs.current.get(next)?.scrollIntoView({ block: "nearest" });
          return next;
        });
      } else if (e.key === "Enter") {
        const ev = filtered[cursor];
        if (ev) onSelect(ev);
      } else if (e.key === "e") {
        e.preventDefault();
        const start = cursor + 1;
        const idx = filtered.findIndex((ev, i) => i >= start && ev.is_error);
        const found = idx >= 0 ? idx : filtered.findIndex((ev) => ev.is_error);
        if (found >= 0) {
          setCursor(found);
          rowRefs.current.get(found)?.scrollIntoView({ block: "center" });
          onSelect(filtered[found]);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [filtered, cursor, onSelect]);

  const toggle = (c: Category) =>
    setHidden((prev) => {
      const next = new Set(prev);
      next.has(c) ? next.delete(c) : next.add(c);
      return next;
    });

  return (
    <div className="timeline">
      <div className="tl-filters">
        {CATEGORIES.map((c) => (
          <button
            key={c}
            className={`tl-chip${hidden.has(c) ? " off" : ""}`}
            onClick={() => toggle(c)}
          >
            {c}
          </button>
        ))}
        <button
          className={`tl-chip err${errorsOnly ? " on" : ""}`}
          onClick={() => setErrorsOnly((v) => !v)}
        >
          errors only
        </button>
      </div>

      <div className="tl-rows">
        {filtered.length === 0 && <div className="empty">No events match the filters.</div>}
        {filtered.map((ev, i) => {
          const selected =
            (ev.tool_use_id && ev.tool_use_id === selectedToolId) || i === cursor;
          if (ev.is_compaction) {
            return (
              <div
                key={`${ev.index}`}
                ref={(el) => {
                  if (el) rowRefs.current.set(i, el);
                  else rowRefs.current.delete(i);
                }}
                className={`tl-compaction${selected ? " selected" : ""}`}
                title={ev.detail ?? undefined}
                onClick={() => {
                  setCursor(i);
                  onSelect(ev);
                }}
              >
                <span className="tl-compaction-label">⎯⎯ {ev.label} ⎯⎯</span>
              </div>
            );
          }
          return (
            <div
              key={`${ev.index}`}
              ref={(el) => {
                if (el) rowRefs.current.set(i, el);
                else rowRefs.current.delete(i);
              }}
              className={
                `tl-row ev-${ev.kind} k-${ev.kind === "tool_result" ? "result" : "row"}` +
                (selected ? " selected" : "") +
                (ev.is_error ? " is-error" : "")
              }
              onClick={() => {
                setCursor(i);
                onSelect(ev);
              }}
            >
              <span className="tl-glyph">{GLYPH[ev.kind]}</span>
              {ev.tool_name && ev.kind === "tool_call" && (
                <span className="tl-tool">{ev.tool_name}</span>
              )}
              <span className="tl-label">{ev.label || ev.type}</span>
              {ev.subagent && <span className="subagent-pill">{ev.subagent.agent_type}</span>}
              {ev.duration_ms != null && <span className="tl-dur">{fmtDur(ev.duration_ms)}</span>}
              {ev.timestamp && (
                <span className="tl-time">
                  {new Date(ev.timestamp).toLocaleTimeString([], { hour12: false })}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
