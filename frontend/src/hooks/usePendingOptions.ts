import { useCallback, useRef, useState } from "react";
import { api } from "../api/client";
import type { PendingOptions } from "../api/types";
import { usePolling } from "./usePolling";

/**
 * Poll a live session for the choices it's currently presenting (permission
 * dialog / AskUserQuestion / ExitPlanMode). Pauses with the tab (usePolling).
 * `select` posts the chosen option with its fingerprint; on a 409 stale-menu the
 * fresh options replace the old ones instead of acting blindly.
 */
export function usePendingOptions(sessionId: string, enabled = true) {
  const [pending, setPending] = useState<PendingOptions | null>(null);
  const [sending, setSending] = useState(false);
  const fpRef = useRef<string>("");

  const refresh = useCallback(async () => {
    const opts = await api.getPendingOptions(sessionId);
    fpRef.current = opts.fingerprint;
    setPending(opts.available ? opts : null);
  }, [sessionId]);

  usePolling(refresh, 1800, enabled);

  const select = useCallback(
    async (optionId: string, freeText?: string) => {
      if (sending) return;
      setSending(true);
      try {
        const res = await api.selectPendingOption(sessionId, optionId, fpRef.current, {
          freeText,
        });
        if (res.ok) {
          setPending(null); // optimistic clear; next poll confirms
        } else if (res.stale && res.options) {
          // The agent moved on — show what's pending now rather than mis-selecting.
          fpRef.current = res.options.fingerprint;
          setPending(res.options.available ? res.options : null);
        }
      } finally {
        setSending(false);
      }
    },
    [sessionId, sending],
  );

  return { pending, sending, select, refresh };
}
