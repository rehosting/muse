import { useEffect } from "react";
import { api } from "../api/client";
import type { ThreadItem, ToolResult } from "../api/types";

interface Handlers {
  onAppend: (items: ThreadItem[]) => void;
  onToolResult: (result: ToolResult) => void;
  onConnectedChange?: (connected: boolean) => void;
}

/**
 * Subscribe to a session's SSE stream. New transcript lines arrive as `append`
 * events; tool results that land for earlier tool_uses arrive as `tool_result`.
 * Only attaches when `enabled` (e.g. the session is running).
 */
export function useSessionStream(
  sessionId: string | undefined,
  enabled: boolean,
  handlers: Handlers,
) {
  useEffect(() => {
    if (!sessionId || !enabled) return;
    const es = new EventSource(api.streamUrl(sessionId));

    es.addEventListener("open", () => handlers.onConnectedChange?.(true));
    es.addEventListener("error", () => handlers.onConnectedChange?.(false));

    es.addEventListener("append", (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      handlers.onAppend(data.items as ThreadItem[]);
    });
    es.addEventListener("tool_result", (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      handlers.onToolResult(data as ToolResult);
    });

    return () => {
      es.close();
      handlers.onConnectedChange?.(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, enabled]);
}
