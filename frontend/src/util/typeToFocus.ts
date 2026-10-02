import { useEffect } from "react";

// Desktop nicety: when you just start typing (a printable key) with nothing
// editable focused, jump into the composer and capture that first character —
// like a terminal or chat client. No-op on modifier combos, and skipped when an
// input/textarea/contenteditable already owns the keystroke.
export function useTypeToFocus(
  ref: React.RefObject<HTMLTextAreaElement | null>,
  append: (ch: string) => void,
  enabled: boolean,
): void {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key.length !== 1) return; // printable single characters only
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || t?.isContentEditable) return;
      const el = ref.current;
      if (!el) return;
      // Focus, then insert the char ourselves — the original keystroke fired on
      // <body>, so it won't land in the newly-focused field on its own.
      e.preventDefault();
      el.focus();
      append(e.key);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ref, append, enabled]);
}
