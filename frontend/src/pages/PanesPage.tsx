import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { TmuxPane, TmuxLayout } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { parseAnsi } from "../util/ansi";

type Status = "needs_you" | "responded" | "working" | "idle";
const RANK: Record<Status, number> = { needs_you: 0, responded: 1, working: 2, idle: 3 };
const SECTION_ORDER: Status[] = ["needs_you", "responded", "working", "idle"];

interface Win {
  key: string;
  session_name: string;
  window_index: number;
  window_name: string;
  panes: TmuxPane[];
  rep: TmuxPane; // representative pane (highest attention / active)
  status: Status;
}

function buildWindows(panes: TmuxPane[]): Win[] {
  const groups = new Map<string, TmuxPane[]>();
  for (const p of panes) {
    const key = `${p.session_name}:${p.window_index}`;
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
  }
  const wins: Win[] = [];
  for (const [key, ps] of groups) {
    const rep = [...ps].sort(
      (a, b) => RANK[a.status] - RANK[b.status] || Number(b.pane_active) - Number(a.pane_active),
    )[0];
    wins.push({
      key,
      session_name: rep.session_name,
      window_index: rep.window_index,
      window_name: rep.window_name,
      panes: ps,
      rep,
      status: rep.status,
    });
  }
  return wins;
}

// Scratch/background: detached sessions, or idle plain shells with no agent.
function isNoise(w: Win): boolean {
  if (!w.rep.session_attached) return true;
  return w.window_name === "bash" && w.status === "idle" && !w.panes.some((p) => p.muse_session_id);
}

const SECTIONS: { status: Status; label: string }[] = [
  { status: "needs_you", label: "Needs you" },
  { status: "responded", label: "Your turn" },
  { status: "working", label: "Working" },
  { status: "idle", label: "Idle" },
];

export default function PanesPage() {
  const [layout, setLayout] = useState<TmuxLayout | null>(null);
  // Selection lives in the URL (?pane=…) so browser/Drive back returns to this exact
  // pane rather than dumping you on the task list.
  const [params, setParams] = useSearchParams();
  const selected = params.get("pane");
  const select = useCallback(
    (id: string | null, replace = false) => setParams(id ? { pane: id } : {}, { replace }),
    [setParams],
  );
  const [showNoise, setShowNoise] = useState(false);

  const refresh = useCallback(async () => {
    setLayout(await api.getTmuxLayout());
  }, []);
  usePolling(refresh, 2500);

  const [creating, setCreating] = useState(false);
  const newSession = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const { pane_id } = await api.newPaneSession();
      await refresh(); // pick up the new window
      select(pane_id); // jump straight into it
    } catch {
      /* ignore — surfaced by a no-op; user can retry */
    } finally {
      setCreating(false);
    }
  };

  const wins = useMemo(() => buildWindows(layout?.panes ?? []), [layout]);
  const visible = wins.filter((w) => !isNoise(w));
  const noise = wins.filter(isNoise);

  // Flat, task-ordered list of panes for the swipeable detail deck. Windows in the
  // same section keep tmux order; panes within a window are adjacent.
  const deckPanes = useMemo(() => {
    const ordered: Win[] = [];
    for (const s of SECTION_ORDER) ordered.push(...visible.filter((w) => w.status === s));
    if (showNoise) ordered.push(...noise);
    return ordered.flatMap((w) => [...w.panes].sort((a, b) => a.pane_index - b.pane_index));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, showNoise]);

  if (!layout) return <div className="empty">Loading tmux…</div>;
  if (!layout.available || layout.panes.length === 0)
    return <div className="empty">{layout.reason ?? "No tmux panes found."}</div>;

  if (selected) {
    if (!deckPanes.some((p) => p.pane_id === selected)) {
      return (
        <div className="empty">
          That pane is gone.{" "}
          <button className="action-btn" onClick={() => select(null)}>
            Back to tasks
          </button>
        </div>
      );
    }
    return (
      <PaneDeck
        panes={deckPanes}
        selected={selected}
        onSelect={(id) => select(id, true)}
        onBack={() => select(null)}
        onAction={refresh}
      />
    );
  }

  return (
    <div className="tasks-page">
      {SECTIONS.map(({ status, label }) => {
        const items = visible.filter((w) => w.status === status);
        if (!items.length) return null;
        return (
          <section key={status} className="tasks-section">
            <h2 className={`tasks-heading tasks-${status}`}>
              {label} <span className="tasks-count">{items.length}</span>
            </h2>
            {items.map((w) => (
              <TaskRow key={w.key} win={w} onOpen={() => select(w.rep.pane_id)} />
            ))}
          </section>
        );
      })}

      {noise.length > 0 && (
        <section className="tasks-section">
          <button className="tasks-noise-toggle" onClick={() => setShowNoise((s) => !s)}>
            {showNoise ? "▾" : "▸"} Scratch &amp; detached{" "}
            <span className="tasks-count">{noise.length}</span>
          </button>
          {showNoise &&
            noise.map((w) => (
              <TaskRow key={w.key} win={w} onOpen={() => select(w.rep.pane_id)} muted />
            ))}
        </section>
      )}

      <button className="task-new" onClick={newSession} disabled={creating}>
        <span className="task-new-plus">{creating ? "…" : "+"}</span>
        {creating ? "Starting Claude…" : "New session"}
      </button>
    </div>
  );
}

function lastLine(preview: string): string {
  const lines = preview.split("\n").filter((l) => l.trim());
  return (lines[lines.length - 1] ?? "").slice(0, 80);
}

function TaskRow({ win, onOpen, muted }: { win: Win; onOpen: () => void; muted?: boolean }) {
  const icon =
    win.status === "needs_you"
      ? "✋"
      : win.status === "responded"
        ? "↩"
        : win.status === "working"
          ? "⟳"
          : "";
  return (
    <button className={`task-row${muted ? " muted" : ""}`} onClick={onOpen}>
      <span className={`task-dot task-${win.status}`} />
      <span className="task-main">
        <span className="task-name">
          {win.window_name || `${win.session_name}:${win.window_index}`}
        </span>
        <span className="task-sub">
          {win.rep.attention || lastLine(win.rep.preview) || win.rep.command}
        </span>
      </span>
      <span className="task-meta">
        <span className="task-cmd">{win.rep.command}</span>
        {win.panes.length > 1 && <span className="task-panes">▦{win.panes.length}</span>}
        {icon && <span className="task-icon">{icon}</span>}
      </span>
    </button>
  );
}

function PaneDeck({
  panes,
  selected,
  onSelect,
  onBack,
  onAction,
}: {
  panes: TmuxPane[];
  selected: string;
  onSelect: (id: string) => void;
  onBack: () => void;
  onAction: () => void;
}) {
  const deckRef = useRef<HTMLDivElement>(null);
  const lastIdx = useRef(-1);
  const idxOf = (id: string) => panes.findIndex((p) => p.pane_id === id);

  // Scroll the deck to the selected pane when it changes from outside (tab tap,
  // back-navigation). The lastIdx guard keeps this from fighting the scroll handler.
  useEffect(() => {
    const el = deckRef.current;
    if (!el) return;
    const i = idxOf(selected);
    if (i >= 0 && i !== lastIdx.current) {
      lastIdx.current = i;
      el.scrollTo({ left: i * el.clientWidth, behavior: "auto" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, panes.length]);

  const onScroll = () => {
    const el = deckRef.current;
    if (!el) return;
    const i = Math.round(el.scrollLeft / el.clientWidth);
    if (i !== lastIdx.current && panes[i]) {
      lastIdx.current = i;
      onSelect(panes[i].pane_id); // replace-mode URL update; doesn't spam history
    }
  };

  // Full tab menu (one per window, deck order), each with a status dot so it shows
  // more than the name. Tapping jumps; the active tab tracks swipes.
  const tabMap = new Map<string, { index: number; name: string; status: Status }>();
  panes.forEach((p, i) => {
    const key = `${p.session_name}:${p.window_index}`;
    const ex = tabMap.get(key);
    if (!ex) tabMap.set(key, { index: i, name: p.window_name || key, status: p.status });
    else if (RANK[p.status] < RANK[ex.status]) ex.status = p.status; // escalate
  });
  const tabs = [...tabMap.entries()].map(([key, t]) => ({ key, ...t }));
  const current = panes[Math.max(0, idxOf(selected))];
  const currentWin = current && `${current.session_name}:${current.window_index}`;

  return (
    <div className="panes-detail">
      <div className="panes-tabbar">
        <button className="drive-back" onClick={onBack} title="Back to tasks">
          ‹
        </button>
        <div className="panes-tabs">
          {tabs.map((t) => (
            <button
              key={t.key}
              className={`panes-tab${t.key === currentWin ? " active" : ""}`}
              onClick={() => onSelect(panes[t.index].pane_id)}
            >
              <span className={`tab-dot task-${t.status}`} />
              {t.name}
            </button>
          ))}
        </div>
      </div>

      <div className="panes-deck" ref={deckRef} onScroll={onScroll}>
        {panes.map((p) => (
          <PaneCard key={p.pane_id} pane={p} onAction={onAction} active={p.pane_id === selected} />
        ))}
      </div>
    </div>
  );
}

// Short labels for Claude Code's permission modes (tap the chip to cycle).
const MODE_LABEL: Record<string, string> = {
  default: "default",
  acceptEdits: "⏵⏵ auto",
  plan: "⏸ plan",
  bypass: "⏵⏵ bypass",
};

function PaneCard({
  pane,
  onAction,
  active = false,
}: {
  pane: TmuxPane;
  onAction: () => void;
  active?: boolean;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const cwdShort = pane.cwd.split("/").slice(-1)[0] || pane.cwd;
  // Parse ANSI once per preview update (not on every keystroke in the composer).
  const segments = useMemo(() => parseAnsi(pane.preview), [pane.preview]);

  // Terminal scroll: default to the bottom (the prompt), but only auto-follow new
  // output when the user is already near the bottom — so scrolling UP to read
  // history isn't yanked back on the next 2.5s poll.
  const screenRef = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  const toBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const el = screenRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, []);
  const onScreenScroll = () => {
    const el = screenRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  // Follow new output when already at the bottom (rAF so the colored spans have
  // laid out and scrollHeight is final).
  useEffect(() => {
    if (stick.current) toBottom();
  }, [pane.preview, toBottom]);
  // Jump to the bottom whenever this card becomes the active tab (open/switch/swipe).
  useEffect(() => {
    if (active) {
      stick.current = true;
      toBottom();
    }
  }, [active, toBottom]);
  // And when the on-screen keyboard opens/resizes the viewport (active card only).
  useEffect(() => {
    if (!active || !window.visualViewport) return;
    const vv = window.visualViewport;
    const onResize = () => {
      if (stick.current) toBottom();
    };
    vv.addEventListener("resize", onResize);
    return () => vv.removeEventListener("resize", onResize);
  }, [active, toBottom]);

  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      await api.sendToPane(pane.pane_id, t);
      setText("");
      onAction();
    } finally {
      setBusy(false);
    }
  };

  const key = async (k: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await api.sendPaneKey(pane.pane_id, k);
      onAction();
    } finally {
      setBusy(false);
    }
  };

  const cycleMode = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.cyclePaneMode(pane.pane_id);
      onAction();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pane-card">
      <div className="pane-card-head">
        <span className="pane-card-loc">
          {pane.session_name}:{pane.window_index}.{pane.pane_index}
        </span>
        <span className="pane-card-cmd">{pane.command}</span>
        <span className="pane-card-cwd" title={pane.cwd}>
          {cwdShort}
        </span>
        {(pane.mode || pane.muse_session_id || pane.context_pct != null) && (
          <span className="pane-card-right">
            {pane.mode && (
              <button
                className={`pane-card-mode mode-${pane.mode}`}
                disabled={busy}
                onClick={cycleMode}
                title="Tap to cycle permission mode (Shift+Tab)"
              >
                {MODE_LABEL[pane.mode] ?? pane.mode}
              </button>
            )}
            {pane.context_pct != null && (
              <span
                className={`pane-card-ctx ctx-${pane.context_pct >= 85 ? "high" : pane.context_pct >= 60 ? "mid" : "low"}`}
                title={`Context window ${Math.round(pane.context_pct)}% full`}
              >
                ctx {Math.round(pane.context_pct)}%
              </span>
            )}
            {pane.muse_session_id && (
              <Link
                className="pane-card-drive"
                to={`/drive/${pane.muse_session_id}`}
                title="Open rich cockpit"
              >
                ▸ drive
              </Link>
            )}
          </span>
        )}
      </div>

      <pre className="pane-card-screen" ref={screenRef} onScroll={onScreenScroll}>
        {pane.preview
          ? segments.map((seg, i) => (
              <span key={i} style={seg.style}>
                {seg.text}
              </span>
            ))
          : "(empty)"}
      </pre>

      {pane.options.length > 0 && (
        <div className="pane-card-options">
          {pane.options.map((o) => (
            <button
              key={o.id}
              className="option-chip"
              disabled={busy}
              onClick={() => key(o.id)}
              title="Send this number to the pane"
            >
              <span className="option-chip-label">
                {o.id}. {o.label}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="pane-card-keys">
        <button className="action-btn" disabled={busy} onClick={() => key("escape")} title="Esc">
          ⎋
        </button>
        <button className="action-btn" disabled={busy} onClick={() => key("up")} title="Up">
          ↑
        </button>
        <button className="action-btn" disabled={busy} onClick={() => key("down")} title="Down">
          ↓
        </button>
        <button className="action-btn" disabled={busy} onClick={() => key("enter")} title="Enter">
          ⏎
        </button>
      </div>

      <div className="pane-card-compose">
        <textarea
          className="reply-input"
          placeholder={`reply to ${pane.window_name || pane.command}…`}
          value={text}
          rows={1}
          onChange={(e) => setText(e.target.value)}
        />
        <button className="action-btn primary" disabled={busy || !text.trim()} onClick={send}>
          Send
        </button>
      </div>
    </div>
  );
}
