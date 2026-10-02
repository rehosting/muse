import { useSyncExternalStore } from "react";

// A real desktop: a fine pointer (mouse/trackpad) on a wide viewport. This is
// what gates the keyboard-first composer behaviour (Enter-to-send, type-to-focus,
// arrow-key pane nav) so it stays DISTINCT from the touch/phone cockpit — where
// Enter means newline and the send button is the only way to fire.
const QUERY = "(pointer: fine) and (min-width: 820px)";

function subscribe(cb: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const m = window.matchMedia(QUERY);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
}

function snapshot(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(QUERY).matches;
}

export function useIsDesktop(): boolean {
  return useSyncExternalStore(
    subscribe,
    snapshot,
    () => false, // SSR / no matchMedia → assume touch
  );
}
