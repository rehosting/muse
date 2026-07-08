import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { SlashCommand } from "../api/types";

/** Detect the active slash query: the whole composer is a single "/token" with no
 * whitespace yet (once you type a space it's arguments, so the menu closes). */
function slashQuery(text: string): string | null {
  const m = /^\/(\S*)$/.exec(text);
  return m ? m[1].toLowerCase() : null;
}

/** Slash-command autocomplete for a composer, à la Claude Code. Wire it into a
 * text input: render `menu` inside a position:relative container and forward key
 * events to `onKeyDown` (which returns true when it consumed the event, so the
 * caller should preventDefault and skip its own handling).
 *
 * The menu is derived purely from `text`: it opens when the whole field is a
 * `/token`, and closes as soon as a space (arguments) or non-slash text appears.
 * Commands load lazily the first time the menu opens for a given pane. */
export function useSlashMenu(opts: {
  paneId: string | undefined;
  text: string;
  setText: (s: string) => void;
  enabled?: boolean;
}): { menu: React.ReactNode; onKeyDown: (e: React.KeyboardEvent) => boolean } {
  const { paneId, text, setText, enabled = true } = opts;
  const [commands, setCommands] = useState<SlashCommand[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const loadedFor = useRef<string | undefined>(undefined);
  // Tap-vs-scroll: a pointer that moves more than a few px is a scroll drag, not
  // a selection — so we can let the list scroll and only pick on a still tap.
  const press = useRef<{ x: number; y: number; moved: boolean } | null>(null);

  const query = slashQuery(text);
  const open = enabled && !!paneId && query !== null && !dismissed;

  // A fresh slash query re-opens the menu and resets the highlight; typing past a
  // "/" (space, more text) clears the manual dismissal so the next "/" works.
  useEffect(() => {
    setSel(0);
    if (query === null) setDismissed(false);
  }, [query]);

  // Lazy-load the command set the first time the menu opens for this pane.
  useEffect(() => {
    if (!open || !paneId || loadedFor.current === paneId) return;
    loadedFor.current = paneId;
    setLoading(true);
    api
      .getPaneCommands(paneId)
      .then(setCommands)
      .catch(() => setCommands([]))
      .finally(() => setLoading(false));
  }, [open, paneId]);

  const matches = useMemo(() => {
    if (!commands || query === null) return [];
    if (!query) return commands;
    // Prefix matches first (what you're typing), then any substring hit.
    const pre = commands.filter((c) => c.name.toLowerCase().startsWith(query));
    const sub = commands.filter(
      (c) => !c.name.toLowerCase().startsWith(query) && c.name.toLowerCase().includes(query),
    );
    return [...pre, ...sub];
  }, [commands, query]);

  const pick = (c: SlashCommand) => {
    setText(`/${c.name} `);
    setDismissed(true); // the query no longer matches, but be explicit
  };

  const onKeyDown = (e: React.KeyboardEvent): boolean => {
    if (!open) return false;
    if (e.key === "Escape") {
      setDismissed(true);
      return true;
    }
    if (matches.length === 0) return false;
    if (e.key === "ArrowDown") {
      setSel((s) => (s + 1) % matches.length);
      return true;
    }
    if (e.key === "ArrowUp") {
      setSel((s) => (s - 1 + matches.length) % matches.length);
      return true;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      pick(matches[Math.min(sel, matches.length - 1)]);
      return true;
    }
    return false;
  };

  const menu = open ? (
    <div className="slash-menu" role="listbox">
      {loading && !commands ? (
        <div className="slash-menu-empty">loading commands…</div>
      ) : matches.length === 0 ? (
        <div className="slash-menu-empty">no matching command</div>
      ) : (
        matches.slice(0, 8).map((c, i) => (
          // A plain div (not a button) so tapping it doesn't steal focus from the
          // composer. We don't preventDefault, so a drag scrolls the list; the pick
          // only fires on a still tap (pointer barely moved between down and up).
          <div
            key={c.name}
            role="option"
            aria-selected={i === sel}
            className={`slash-menu-row${i === sel ? " active" : ""}`}
            onPointerDown={(e) => {
              press.current = { x: e.clientX, y: e.clientY, moved: false };
            }}
            onPointerMove={(e) => {
              const p = press.current;
              if (p && Math.hypot(e.clientX - p.x, e.clientY - p.y) > 8) p.moved = true;
            }}
            onPointerUp={() => {
              const p = press.current;
              press.current = null;
              if (p && !p.moved) pick(c);
            }}
          >
            <span className="slash-menu-name">/{c.name}</span>
            {c.source !== "builtin" && (
              <span className={`slash-menu-src src-${c.source}`}>{c.source}</span>
            )}
            {c.description && <span className="slash-menu-desc">{c.description}</span>}
          </div>
        ))
      )}
    </div>
  ) : null;

  return { menu, onKeyDown };
}
