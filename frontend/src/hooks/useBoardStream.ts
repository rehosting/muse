import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { BoardCard, BoardSnapshot } from "../api/types";
import { usePolling } from "./usePolling";

/** Live board state over one multiplexed SSE connection: `snapshot` replaces
 * the card map, `cards` events upsert/remove. Falls back to polling
 * GET /api/board when EventSource errors (proxy trouble, server restart) and
 * keeps retrying SSE in the background. Closes the stream while the tab is
 * hidden (mirrors usePolling's philosophy); snapshot-on-connect makes the
 * reconnect trivially consistent. */
export function useBoardStream(): {
  cards: BoardCard[] | null;
  live: boolean; // true = SSE connected, false = polling fallback
} {
  const [cards, setCards] = useState<BoardCard[] | null>(null);
  const [sseUp, setSseUp] = useState(false);
  const mapRef = useRef<Map<string, BoardCard>>(new Map());
  const sseUpRef = useRef(false);

  const apply = () => {
    const arr = [...mapRef.current.values()];
    const order: Record<string, number> = { waiting: 0, live: 1, stopped: 2 };
    arr.sort(
      (a, b) =>
        (order[a.state] ?? 3) - (order[b.state] ?? 3) ||
        b.mtime.localeCompare(a.mtime),
    );
    setCards(arr);
  };

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: number | undefined;
    let stopped = false;

    const setSnapshot = (snap: BoardSnapshot) => {
      mapRef.current = new Map(snap.cards.map((c) => [c.session_id, c]));
      apply();
    };

    const connect = () => {
      if (stopped || document.hidden) return;
      es = new EventSource("/api/board/stream");
      es.addEventListener("snapshot", (e) => {
        sseUpRef.current = true;
        setSseUp(true);
        setSnapshot(JSON.parse((e as MessageEvent).data));
      });
      es.addEventListener("cards", (e) => {
        const delta = JSON.parse((e as MessageEvent).data) as {
          updated: BoardCard[];
          removed: string[];
        };
        for (const c of delta.updated) mapRef.current.set(c.session_id, c);
        for (const sid of delta.removed) mapRef.current.delete(sid);
        apply();
      });
      es.onerror = () => {
        es?.close();
        es = null;
        sseUpRef.current = false;
        setSseUp(false);
        if (!stopped) retry = window.setTimeout(connect, 5000);
      };
    };

    const onVisibility = () => {
      if (document.hidden) {
        es?.close();
        es = null;
        sseUpRef.current = false;
        setSseUp(false);
      } else if (!es) {
        connect();
      }
    };

    connect();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      es?.close();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // Polling fallback — only does work while SSE is down.
  usePolling(async () => {
    if (sseUpRef.current) return;
    const snap = await api.getBoard();
    mapRef.current = new Map(snap.cards.map((c) => [c.session_id, c]));
    apply();
  }, 5000);

  return { cards, live: sseUp };
}
