// Desktop terminal passthrough: classify a keyboard event into what the /panes deck should
// do with it when a pane is open in terminal view and the composer isn't focused. Pure and
// unit-tested — the PaneCard listener just executes the returned Action.

/** Browser KeyboardEvent.key → the base name the tmux key endpoint understands
 * (see backend `_resolve_key` / `_KEYS`). Printable single chars pass through as-is. */
export const NAMED_EVENT_KEYS: Record<string, string> = {
  Enter: "enter",
  Tab: "tab",
  Escape: "escape",
  Backspace: "backspace",
  Delete: "delete",
  " ": "space",
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
  Home: "home",
  End: "end",
  PageUp: "pageup",
  PageDown: "pagedown",
};

export function eventKeyToName(k: string): string | null {
  if (NAMED_EVENT_KEYS[k]) return NAMED_EVENT_KEYS[k];
  return k.length === 1 ? k : null;
}

export interface KeyEventLike {
  key: string;
  ctrlKey: boolean;
  altKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

export type TermAction =
  | { kind: "char"; ch: string } // a printable char to buffer + send literally
  | { kind: "key"; key: string } // a composed key name (e.g. "enter", "c-c", "m-b", "s-tab")
  | { kind: "prevPane" }
  | { kind: "nextPane" }
  | { kind: "back" }
  | { kind: "ignore" }; // leave it to the browser/OS

/** Decide what a keystroke means in terminal-passthrough mode. Carveouts: Cmd/Meta and the
 * browser-destructive Ctrl-W/T/N are left to the browser; Alt+←/→ step panes; Alt+Esc exits;
 * everything else (printables, Enter/Tab/Esc/arrows/…, terminal Ctrl-combos) → the session. */
export function classifyKey(e: KeyEventLike): TermAction {
  if (e.metaKey) return { kind: "ignore" }; // Cmd/Win combos belong to the OS/browser
  if (e.altKey && e.key === "ArrowLeft") return { kind: "prevPane" };
  if (e.altKey && e.key === "ArrowRight") return { kind: "nextPane" };
  if (e.altKey && e.key === "Escape") return { kind: "back" };
  // Don't hijack the browser's tab-management shortcuts.
  if (e.ctrlKey && !e.altKey && ["w", "t", "n"].includes(e.key.toLowerCase())) {
    return { kind: "ignore" };
  }
  // A printable character with no Ctrl. Alt → meta-prefixed key; otherwise a literal char.
  if (e.key.length === 1 && !e.ctrlKey) {
    return e.altKey ? { kind: "key", key: "m-" + e.key } : { kind: "char", ch: e.key };
  }
  // Named keys, and Ctrl-combos (Ctrl+letter lands here since it isn't a bare printable).
  const base = eventKeyToName(e.key);
  if (base == null) return { kind: "ignore" }; // F-keys, dead keys, IME, …
  const prefix =
    (e.ctrlKey ? "c-" : "") +
    (e.altKey ? "m-" : "") +
    (e.shiftKey && base.length > 1 ? "s-" : ""); // shift already baked into printable chars
  return { kind: "key", key: prefix + base };
}
