import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Thread, ThreadItem } from "../api/types";
import { usePolling } from "../hooks/usePolling";

/** Reader mode's accumulated view of a session: a contiguous window
 * [start, start+items.length) of the full thread. Tail polls refresh the end;
 * load-earlier prepends history. */
export interface ReaderState {
  items: ThreadItem[];
  start: number; // full-thread index of items[0]
  total: number;
  cwd: string | null;
}

export const TAIL_LIMIT = 40;
export const EARLIER_LIMIT = 60;

/** Cheap change detector for a fetched tail window: new messages change the uuid
 * list; a tool result landing on an existing item changes its result count.
 * Callers skip the state update entirely when the signature is unchanged —
 * replacing React state with identical content re-renders the whole conversation
 * and is what caused the reader's scroll to jump. */
export function tailSig(items: ThreadItem[]): string {
  return items
    .map(
      (it) =>
        it.uuid + ":" + it.blocks.reduce((n, b) => n + (b.tool_use?.result ? 1 : 0), 0),
    )
    .join("|");
}

/** Fold a freshly-fetched TAIL window into the accumulated state.
 *
 * - First load (or the conversation advanced past our window): adopt the window.
 * - Otherwise: keep every accumulated item BEFORE the window untouched (same
 *   object identities → React leaves their DOM alone) and swap in the fetched
 *   items for the overlap + new tail (tail items mutate as tool results land).
 */
export function mergeTail(prev: ReaderState | null, win: Thread): ReaderState {
  const ws = win.window_start ?? 0;
  const total = win.total_items ?? win.items.length;
  if (!prev || ws > prev.start + prev.items.length) {
    return { items: win.items, start: ws, total, cwd: win.project_cwd };
  }
  const keep = prev.items.slice(0, Math.max(0, ws - prev.start));
  return { items: [...keep, ...win.items], start: prev.start, total, cwd: win.project_cwd };
}

/** Fold an EARLIER window (fetched with before=prev.start) in front of the
 * accumulated state. The server returns the contiguous window ending exactly at
 * prev.start, so this is a pure prepend. */
export function prependEarlier(prev: ReaderState, win: Thread): ReaderState {
  return {
    ...prev,
    items: [...win.items, ...prev.items],
    start: win.window_start ?? 0,
  };
}

/** The reader's data layer: poll the tail of `sid`'s thread while `enabled`,
 * accumulate history on demand, and never churn state when nothing changed.
 * Scroll behavior stays with the component; this hook only owns the data. */
export function useReaderThread(sid: string | null, enabled: boolean, intervalMs = 3500) {
  const [reader, setReader] = useState<ReaderState | null>(null);
  const [pullingEarlier, setPullingEarlier] = useState(false);
  // null = "never fetched" so the FIRST tail always applies — even an empty thread,
  // whose signature is "" (which would otherwise match a "" seed and get skipped,
  // leaving `reader` null and the UI stuck on "loading conversation…" forever).
  const lastSig = useRef<string | null>(null);
  const startRef = useRef(0);
  startRef.current = reader?.start ?? 0;
  const pulling = useRef(false);

  const fetchTail = useCallback(async () => {
    if (!sid) return;
    // anchor MUST be explicit: the server's default flips to the HEAD window
    // 30s after the last transcript write — i.e. exactly when a session is
    // sitting idle waiting for you, which is when you open it.
    let win: Thread;
    try {
      win = await api.getThread(sid, { limit: TAIL_LIMIT, anchor: "tail" });
    } catch {
      // A brand-new session isn't in the thread index yet (the endpoint 404s until it
      // reparses), so getThread throws. Show it as empty rather than a perpetual
      // "loading conversation…" spinner; polling continues and self-heals once the
      // first messages land. Only seed empty when we have nothing — never clobber an
      // already-loaded thread on a transient error.
      setReader((prev) => prev ?? { items: [], start: 0, total: 0, cwd: null });
      return;
    }
    const sig = tailSig(win.items);
    if (sig === lastSig.current) return; // unchanged → no state churn, no jump
    lastSig.current = sig;
    setReader((prev) => mergeTail(prev, win));
  }, [sid]);

  useEffect(() => {
    setReader(null);
    lastSig.current = null;
    // The poll timer doesn't restart on a sid change (only on enabled/interval),
    // so fetch the new session's tail immediately instead of waiting a tick.
    if (enabled && sid) fetchTail().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid]);

  usePolling(fetchTail, intervalMs, enabled && !!sid);

  /** Fetch the window just before what we have. Returns true if items were
   * prepended (the caller restores its scroll position on state change). */
  const loadEarlier = useCallback(async (): Promise<boolean> => {
    if (!sid || pulling.current || startRef.current <= 0) return false;
    pulling.current = true;
    setPullingEarlier(true);
    try {
      const win = await api.getThread(sid, { limit: EARLIER_LIMIT, before: startRef.current });
      setReader((prev) => (prev ? prependEarlier(prev, win) : prev));
      return true;
    } finally {
      pulling.current = false;
      setPullingEarlier(false);
    }
  }, [sid]);

  return { reader, loadEarlier, pullingEarlier };
}
