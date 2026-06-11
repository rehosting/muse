import { useEffect, useRef } from "react";

/**
 * Poll `fn` without overlap: run it, wait for it to finish, then schedule the
 * next run `intervalMs` later. Avoids the request pile-up that setInterval causes
 * when a response takes longer than the interval (which snowballs server load).
 *
 * Pauses entirely while the tab is hidden (visibilitychange) so a forgotten
 * background tab can't keep hammering the server, and resumes immediately when
 * the tab becomes visible again.
 */
export function usePolling(fn: () => Promise<unknown>, intervalMs: number, enabled = true) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: number | undefined;
    const hidden = () => typeof document !== "undefined" && document.hidden;
    const tick = async () => {
      if (stopped || hidden()) return; // paused; visibilitychange resumes us
      try {
        await fnRef.current();
      } catch {
        /* ignore; try again next tick */
      }
      if (!stopped && !hidden()) timer = window.setTimeout(tick, intervalMs);
    };
    const onVisibility = () => {
      if (!hidden() && !stopped) {
        if (timer) clearTimeout(timer);
        tick(); // resume right away when the tab comes back
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, enabled]);
}
