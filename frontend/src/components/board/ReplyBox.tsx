import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { useIsDesktop } from "../../util/useIsDesktop";
import { useTypeToFocus } from "../../util/typeToFocus";
import AiActionButton from "../AiActionButton";
import { useSlashMenu } from "../SlashMenu";

/** Reply to a live Claude session from the board: text → its tmux pane.
 * Disabled (with the reason) when the session has no matched pane. */
export default function ReplyBox({
  sessionId,
  hasPane,
  busy,
  variant = "board",
  draft,
  onQueued,
  onSent,
  showInterrupt = true,
  slashPaneId,
  captureTyping = false,
}: {
  sessionId: string;
  hasPane: boolean;
  busy: boolean; // live_status === "busy": sending mid-turn steers the current turn
  variant?: "board" | "cockpit"; // cockpit = phone-first multiline textarea
  draft?: string; // when set, prefills the box (AI suggestion) — you edit and send
  onQueued?: () => void; // notify the parent so its queue chips refresh immediately
  onSent?: () => void; // notify the parent so it can refresh sooner than its poll
  showInterrupt?: boolean; // hide the Esc button where the parent already has one
  slashPaneId?: string; // pane id to source "/" autocomplete from (enables the menu)
  captureTyping?: boolean; // desktop: start-typing-anywhere focuses this composer
}) {
  const desktop = useIsDesktop();
  const [text, setText] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "queued" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const sentTimer = useRef<number | undefined>(undefined);
  // What a tap on the primary button does; the last option picked from the menu
  // sticks, so a plain tap repeats it. Long-pressing opens the menu of send modes.
  const [mode, setMode] = useState<"send" | "queue">("send");
  const [menuOpen, setMenuOpen] = useState(false);
  const pressTimer = useRef<number | undefined>(undefined);
  const longFired = useRef(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(
    () => () => {
      window.clearTimeout(sentTimer.current);
      window.clearTimeout(pressTimer.current);
    },
    [],
  );
  // A parent-supplied draft is a DEFAULT, not an override: apply it only when the
  // box is empty or still holds the previous default (i.e. the user hasn't typed
  // their own text). This lets the suggestion refresh each turn without ever
  // clobbering in-progress input. Send then sends whatever's in the box — so an
  // untouched default sends as-is.
  const lastDraft = useRef("");
  useEffect(() => {
    if (!draft) return;
    setText((cur) => (cur === "" || cur === lastDraft.current ? draft : cur));
    lastDraft.current = draft;
  }, [draft]);

  const send = async () => {
    const t = text.trim();
    if (!t || state === "sending") return;
    setState("sending");
    setError(null);
    try {
      await api.respondToSession(sessionId, t);
      setText("");
      setState("sent");
      onSent?.();
      sentTimer.current = window.setTimeout(() => setState("idle"), 2500);
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // "Queue" hands the message to muse to type in when the turn ACTUALLY ends
  // (idle, no permission menu) — vs Send-while-busy, which steers the current turn.
  const queue = async () => {
    const t = text.trim();
    if (!t || state === "sending") return;
    setState("sending");
    setError(null);
    try {
      await api.queueReply(sessionId, t);
      setText("");
      setState("queued");
      onQueued?.();
      sentTimer.current = window.setTimeout(() => setState("idle"), 2500);
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // "Compact first": send /compact, then send this reply right behind it. No need
  // to wait for idle — Claude Code's own input queue holds the reply until the
  // compaction turn finishes, so it lands right after the fresh summary.
  const compactThenSend = async () => {
    if (state === "sending") return;
    const t = text.trim();
    setState("sending");
    setError(null);
    try {
      await api.respondToSession(sessionId, "/compact");
      if (t) await api.respondToSession(sessionId, t);
      setText("");
      setState("sent");
      onSent?.();
      sentTimer.current = window.setTimeout(() => setState("idle"), 2500);
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const interrupt = async () => {
    if (!window.confirm("Send Esc to interrupt this session's current turn?")) return;
    try {
      await api.sendSessionKey(sessionId, "escape");
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Primary button: tap runs the current mode; a long press (no drag needed)
  // opens the send-options menu instead of firing, so a stray click after it is
  // swallowed.
  const startPress = () => {
    longFired.current = false;
    pressTimer.current = window.setTimeout(() => {
      longFired.current = true;
      setMenuOpen(true);
      navigator.vibrate?.(15);
    }, 450);
  };
  const endPress = () => window.clearTimeout(pressTimer.current);
  const primary = () => {
    if (longFired.current) {
      longFired.current = false; // this "click" was the end of a long press — ignore
      return;
    }
    if (mode === "queue") queue();
    else send();
  };
  // Pick a way to send from the long-press menu: set the sticky mode (so the next
  // plain tap repeats it) and fire it now.
  const pick = (choice: "send" | "queue" | "compact") => {
    setMenuOpen(false);
    if (choice === "compact") {
      compactThenSend();
      return;
    }
    setMode(choice);
    (choice === "queue" ? queue : send)();
  };

  const slash = useSlashMenu({ paneId: slashPaneId, text, setText });

  // Desktop: begin typing anywhere on the card and land in this composer.
  const append = useCallback((ch: string) => setText((t) => t + ch), []);
  useTypeToFocus(taRef, append, desktop && captureTyping && hasPane);

  // Multi-line: grow the textarea with its content (cockpit only), capped so a
  // long paste scrolls internally instead of eating the whole card.
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  // Keyboard-drive the send-options menu: focus the first row when it opens.
  useEffect(() => {
    if (menuOpen) menuRef.current?.querySelector<HTMLButtonElement>(".send-menu-row")?.focus();
  }, [menuOpen]);
  const onMenuKey = (e: React.KeyboardEvent) => {
    const rows = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(".send-menu-row") ?? [],
    );
    const at = rows.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") {
      e.preventDefault();
      setMenuOpen(false);
      taRef.current?.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      rows[(at + 1) % rows.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      rows[(at - 1 + rows.length) % rows.length]?.focus();
    }
  };

  const placeholder = hasPane
    ? busy
      ? "⏳ queue for when this turn ends, or send to steer now…"
      : "reply to this session…"
    : "no tmux pane matched — open it in your terminal";

  return (
    <div className={variant === "cockpit" ? "reply-box reply-box-cockpit" : "reply-box"}>
      {slash.menu}
      {variant === "cockpit" ? (
        <textarea
          ref={taRef}
          className="reply-input"
          placeholder={placeholder}
          value={text}
          disabled={!hasPane}
          rows={1}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (slash.onKeyDown(e)) {
              e.preventDefault();
              return;
            }
            // Desktop: Enter sends, Shift+Enter is a newline. On touch, Enter is
            // always a newline (the send button fires) — keep the views distinct.
            if (desktop && e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              mode === "queue" ? queue() : send();
            }
          }}
        />
      ) : (
        <input
          className="reply-input"
          placeholder={placeholder}
          value={text}
          disabled={!hasPane}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // Let the slash menu claim arrows/Enter/Esc/Tab first.
            if (slash.onKeyDown(e)) {
              e.preventDefault();
              return;
            }
            if (e.key === "Enter") (mode === "queue" ? queue : send)();
          }}
        />
      )}
      <div className="reply-primary-wrap">
        {menuOpen && (
          <>
            {/* Tap-away catcher so the menu dismisses without also hitting the UI behind it. */}
            <div
              className="send-menu-scrim"
              onPointerDown={(e) => {
                e.preventDefault();
                setMenuOpen(false);
              }}
            />
            <div className="send-menu" role="menu" ref={menuRef} onKeyDown={onMenuKey}>
              <button className="send-menu-row" onClick={() => pick("send")}>
                <span className="send-menu-icon">➤</span>
                <span className="send-menu-label">Send now</span>
                <span className="send-menu-sub">
                  {busy ? "steer the current turn" : "deliver immediately"}
                </span>
              </button>
              <button className="send-menu-row" onClick={() => pick("queue")}>
                <span className="send-menu-icon">⏳</span>
                <span className="send-menu-label">Queue</span>
                <span className="send-menu-sub">deliver when the turn ends</span>
              </button>
              <button className="send-menu-row" onClick={() => pick("compact")}>
                <span className="send-menu-icon">🗜</span>
                <span className="send-menu-label">Compact first</span>
                <span className="send-menu-sub">/compact, then send this reply</span>
              </button>
            </div>
          </>
        )}
        {desktop && (
          // Desktop path to the send modes (touch uses long-press). Caret ▾ opens
          // the same menu — Send now / Queue / Compact first.
          <button
            type="button"
            className="reply-send-caret"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Send options"
            title="Send options — Send now / Queue / Compact first"
            disabled={!hasPane}
            onClick={() => setMenuOpen((o) => !o)}
          >
            ▾
          </button>
        )}
        <button
          className={`action-btn primary reply-primary reply-send-icon${mode === "queue" ? " reply-mode-queue" : ""}`}
          // Enabled even when empty so the mode can be flipped before typing; the
          // send/queue handlers no-op on empty text.
          disabled={!hasPane || state === "sending"}
          aria-label={mode === "queue" ? "Queue" : "Send"}
          title={
            mode === "queue"
              ? "Queue — delivered when the turn ends. Long-press for send options."
              : busy
                ? "Send now — steers the in-progress turn. Long-press for send options."
                : "Send. Long-press for send options."
          }
          onPointerDown={startPress}
          onPointerUp={endPress}
          onPointerLeave={endPress}
          onClick={primary}
        >
          {state === "sending"
            ? "…"
            : state === "sent" || state === "queued"
              ? "✓"
              : mode === "queue"
                ? "⏳"
                : "➤"}
        </button>
      </div>
      {showInterrupt && (
        <button
          className="action-btn reply-esc"
          disabled={!hasPane}
          title="Interrupt the current turn (sends Esc to the pane)"
          onClick={interrupt}
        >
          Esc
        </button>
      )}
      {variant !== "cockpit" && (
        <AiActionButton
          label="✦"
          title="AI-draft a reply (prefills the box — you edit and send)"
          enqueue={() => api.draftReply(sessionId)}
          onDone={(job) => {
            const draft = (job.result as { draft?: string } | null)?.draft;
            if (draft) setText(draft);
          }}
        />
      )}
      {state === "error" && error && <div className="reply-error">⚠ {error}</div>}
    </div>
  );
}
