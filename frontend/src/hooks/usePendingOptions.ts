import { useCallback, useRef, useState } from "react";
import { api } from "../api/client";
import type { PendingOptions } from "../api/types";
import { usePolling } from "./usePolling";

/**
 * Poll a live session for the choices it's currently presenting (permission
 * dialog / AskUserQuestion / ExitPlanMode). Pauses with the tab (usePolling).
 * `select` posts the chosen option with its fingerprint; on a 409 stale-menu the
 * fresh options replace the old ones instead of acting blindly.
 *
 * `dismiss` hides the current prompt WITHOUT answering it — screen parsing has
 * false positives, and a wrong chip row shouldn't be stuck on screen. It's keyed
 * to the fingerprint, so dismissing this prompt never suppresses the next one:
 * anything the session asks afterwards hashes differently and shows again.
 */
export function usePendingOptions(sessionId: string, enabled = true) {
  const [pending, setPending] = useState<PendingOptions | null>(null);
  const [sending, setSending] = useState(false);
  const [dismissedFp, setDismissedFp] = useState("");
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

  const dismiss = useCallback(() => {
    setDismissedFp(fpRef.current);
  }, []);

  // Hidden, not dropped: a later poll with the same fingerprint stays hidden,
  // while a different prompt (or the same one re-asked after the screen moved)
  // has a new fingerprint and surfaces normally.
  const visible = pending && pending.fingerprint !== dismissedFp ? pending : null;

  return { pending: visible, sending, select, refresh, dismiss };
}
