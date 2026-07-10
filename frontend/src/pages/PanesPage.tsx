import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { LayoutSnapshot, PaneScreen, Profile, TmuxPane, TmuxLayout } from "../api/types";
import ConversationView from "../components/ConversationView";
import OptionPicker from "../components/OptionPicker";
import QueueChips from "../components/board/QueueChips";
import ReplyBox from "../components/board/ReplyBox";
import { useSlashMenu } from "../components/SlashMenu";
import { usePendingOptions } from "../hooks/usePendingOptions";
import { usePolling } from "../hooks/usePolling";
import { parseAnsi } from "../util/ansi";
import { useReaderThread } from "../util/reader";
import { useIsDesktop } from "../util/useIsDesktop";
import { useLongPress } from "../util/useLongPress";
import { classifyKey, eventKeyToName } from "../util/termKeys";
import { useTypeToFocus } from "../util/typeToFocus";

type Status = "needs_you" | "responded" | "working" | "idle";
const RANK: Record<Status, number> = { needs_you: 0, responded: 1, working: 2, idle: 3 };
const SECTION_ORDER: Status[] = ["needs_you", "responded", "working", "idle"];
const PROVIDER_LABEL: Record<string, string> = {
  claude: "Claude",
  gemini: "Gemini",
  codex: "Codex",
  opencode: "OpenCode",
};

export interface Win {
  key: string;
  session_name: string;
  window_index: number;
  window_name: string;
  panes: TmuxPane[];
  rep: TmuxPane; // representative pane (highest attention / active)
  status: Status;
  last_activity: number; // most-recent activity across the window's panes
}

type SortMode = "attention" | "recent" | "alpha";
const SORTS: { mode: SortMode; label: string }[] = [
  { mode: "attention", label: "Attention" },
  { mode: "recent", label: "Recent" },
  { mode: "alpha", label: "A–Z" },
];

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

export async function waitForPane(
  getLayout: () => Promise<TmuxLayout>,
  paneId: string,
  attempts = 20,
  delayMs = 250,
): Promise<TmuxLayout> {
  let layout = await getLayout();
  for (let i = 1; i < attempts && !layout.panes.some((p) => p.pane_id === paneId); i += 1) {
    await sleep(delayMs);
    layout = await getLayout();
  }
  return layout;
}

// Order windows for the current sort. "attention" keeps the urgent-first grouping
// (ties broken by recency); "recent" and "alpha" are flat reorderings.
function sortWins(wins: Win[], mode: SortMode): Win[] {
  const out = [...wins];
  const name = (w: Win) => (w.window_name || w.session_name).toLowerCase();
  if (mode === "recent") out.sort((a, b) => b.last_activity - a.last_activity);
  else if (mode === "alpha") out.sort((a, b) => name(a).localeCompare(name(b)));
  else out.sort((a, b) => RANK[a.status] - RANK[b.status] || b.last_activity - a.last_activity);
  return out;
}

export function buildWindows(panes: TmuxPane[]): Win[] {
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
      last_activity: Math.max(0, ...ps.map((p) => p.last_activity ?? 0)),
    });
  }
  return wins;
}

// A group is a tmux session: the windows (Win) that share a session_name. The rail
// lists these so you can filter the fleet to one group and move windows between them.
export interface Group {
  name: string; // tmux session_name
  windows: number; // window count in the session
  status: Status; // most-urgent status across its windows
  last_activity: number;
  placeholder: boolean; // every pane is an idle non-agent shell → safe to delete
}

export function buildGroups(wins: Win[]): Group[] {
  const by = new Map<string, Win[]>();
  for (const w of wins) (by.get(w.session_name) ?? by.set(w.session_name, []).get(w.session_name)!).push(w);
  const groups: Group[] = [];
  for (const [name, ws] of by) {
    groups.push({
      name,
      windows: ws.length,
      status: ws.reduce<Status>((s, w) => (RANK[w.status] < RANK[s] ? w.status : s), "idle"),
      last_activity: Math.max(0, ...ws.map((w) => w.last_activity)),
      placeholder: ws.every(isNoise), // only idle scratch shells → deletable
    });
  }
  // Attention-first, then most-recent — matches the task list's default ordering.
  groups.sort((a, b) => RANK[a.status] - RANK[b.status] || b.last_activity - a.last_activity);
  return groups;
}

// Scratch/background: a window with no tracked or known agent provider that's just sitting idle.
// (We deliberately ignore tmux's session_attached here — when you're driving from a
// phone no terminal client is attached, so *every* session reads as detached; that
// flag says nothing about whether the session is worth your attention.)
function isNoise(w: Win): boolean {
  const hasAgent = w.panes.some((p) => p.muse_session_id || p.provider);
  return !hasAgent && w.status === "idle";
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
  const [sortMode, setSortMode] = useState<SortMode>(
    () => (localStorage.getItem("panesSort") as SortMode) || "attention",
  );
  const setSort = useCallback((m: SortMode) => {
    setSortMode(m);
    localStorage.setItem("panesSort", m);
  }, []);
  const desktop = useIsDesktop();
  // Active group (tmux session) filter, null = All. Survives reloads.
  const [group, setGroupState] = useState<string | null>(
    () => localStorage.getItem("panesGroup") || null,
  );
  const setGroup = useCallback((g: string | null) => {
    setGroupState(g);
    if (g) localStorage.setItem("panesGroup", g);
    else localStorage.removeItem("panesGroup");
  }, []);
  // Findability: quick name filter + pinned windows (both survive reloads).
  const [filter, setFilter] = useState("");
  const [pins, setPins] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem("panesPins") || "[]") as string[]);
    } catch {
      return new Set();
    }
  });
  const togglePin = useCallback((key: string) => {
    setPins((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      localStorage.setItem("panesPins", JSON.stringify([...next]));
      return next;
    });
  }, []);

  // Poll the SLIM layout (no screen text): status/attention/menus for every pane
  // at a phone-friendly payload. The deck fetches live screens per visible pane.
  const refresh = useCallback(async () => {
    setLayout(await api.getTmuxLayout(false));
  }, []);
  usePolling(refresh, 2500);

  const [creating, setCreating] = useState(false);
  // Launch a window from a profile, into the currently-filtered group (null "All" →
  // backend picks the default session). Params were collected by the launcher.
  const launch = useCallback(
    async (name: string, values: Record<string, string>) => {
      if (creating) return;
      setCreating(true);
      try {
        const { pane_id } = await api.launchProfile(name, values, group);
        const fresh = await waitForPane(() => api.getTmuxLayout(false), pane_id);
        setLayout(fresh);
        // Follow the new window into view before selecting it. The deck is scoped to the
        // active name/group filter, so if either would exclude the new pane, selecting it
        // just flashes "That pane is gone". Clear the text filter and switch the group
        // filter to whatever session the window actually landed in (same idea as deckMove).
        const landed = fresh.panes.find((p) => p.pane_id === pane_id);
        setFilter("");
        if (landed) {
          if (group !== null && landed.session_name !== group) setGroup(landed.session_name);
          select(pane_id); // jump straight into it
        }
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Could not launch window");
      } finally {
        setCreating(false);
      }
    },
    [creating, group, select, setGroup],
  );
  const launchCodexFromPane = useCallback(
    async (pane: TmuxPane) => {
      if (creating || !pane.muse_session_id) return;
      setCreating(true);
      try {
        const { pane_id } = await api.launchCodexFromSession({
          source_session_id: pane.muse_session_id,
          cwd: pane.cwd,
          session: pane.session_name,
          window_name: pane.window_name,
        });
        const fresh = await waitForPane(() => api.getTmuxLayout(false), pane_id);
        setLayout(fresh);
        const landed = fresh.panes.find((p) => p.pane_id === pane_id);
        setFilter("");
        if (landed) {
          if (group !== null && landed.session_name !== group) setGroup(landed.session_name);
          select(pane_id);
        }
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Could not launch Codex session");
      } finally {
        setCreating(false);
      }
    },
    [creating, group, select, setGroup],
  );

  const wins = useMemo(() => buildWindows(layout?.panes ?? []), [layout]);
  const groups = useMemo(() => buildGroups(wins), [wins]);
  // If the active group disappears (renamed, emptied, killed), fall back to All.
  useEffect(() => {
    if (group !== null && layout && !groups.some((g) => g.name === group)) setGroup(null);
  }, [group, groups, layout, setGroup]);

  // Group operations — muse runs the tmux verb, then we re-poll to reflect it.
  const moveWindow = useCallback(
    async (windowId: string, session: string) => {
      try {
        await api.moveTmuxWindow(windowId, session);
        await refresh();
      } catch {
        /* transient — next poll self-corrects */
      }
    },
    [refresh],
  );
  const moveToNewGroup = useCallback(
    async (windowId: string) => {
      const name = window.prompt("Move to a new group named:")?.trim();
      if (!name) return;
      try {
        await api.createTmuxSession(name);
        await api.moveTmuxWindow(windowId, name);
        setGroup(name);
        await refresh();
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Couldn't create the group.");
      }
    },
    [refresh, setGroup],
  );
  // Deck variants: when you recategorize the window you're LOOKING AT, the filtered
  // deck would drop it and flash the "pane is gone" guard. So set the fresh layout
  // and the group filter together (one render, React batches them) — the deck
  // "follows" the window into its new group. (The list handlers above don't follow.)
  const deckMove = useCallback(
    async (windowId: string, session: string) => {
      try {
        await api.moveTmuxWindow(windowId, session);
        const fresh = await api.getTmuxLayout(false);
        setLayout(fresh);
        if (group !== null) setGroup(session); // only meaningful when a filter is active
      } catch {
        /* transient — next poll self-corrects */
      }
    },
    [group, setGroup],
  );
  const deckMoveToNewGroup = useCallback(
    async (windowId: string) => {
      const name = window.prompt("Move to a new group named:")?.trim();
      if (!name) return;
      try {
        await api.createTmuxSession(name);
        await api.moveTmuxWindow(windowId, name);
        const fresh = await api.getTmuxLayout(false);
        setLayout(fresh);
        setGroup(name); // always focus the brand-new group
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Couldn't create the group.");
      }
    },
    [setGroup],
  );
  const renameWindow = useCallback(
    async (windowId: string, currentName: string) => {
      const next = window.prompt("Rename window to:", currentName)?.trim();
      if (!next || next === currentName) return;
      try {
        await api.renameTmuxWindow(windowId, next);
        await refresh();
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Couldn't rename the window.");
      }
    },
    [refresh],
  );
  // Remove (close) a session: fetch the profile-cleanup preview, then open a confirm
  // dialog. confirmRemove kills the window and (opt-in) runs cleanup, then refreshes; if
  // the removed window held the pane we're viewing, drop back to the list.
  const [pendingRemove, setPendingRemove] = useState<{
    windowId: string;
    name: string;
    cleanup: { profile: string; command: string } | null;
  } | null>(null);
  const removeWindow = useCallback(async (windowId: string, name: string) => {
    let cleanup: { profile: string; command: string } | null = null;
    try {
      cleanup = (await api.getWindowCleanup(windowId)).cleanup;
    } catch {
      /* preview is best-effort — still allow the removal */
    }
    setPendingRemove({ windowId, name, cleanup });
  }, []);
  const confirmRemove = useCallback(
    async (runCleanup: boolean) => {
      if (!pendingRemove) return;
      const { windowId } = pendingRemove;
      setPendingRemove(null);
      try {
        const res = await api.closeTmuxWindow(windowId, runCleanup);
        const fresh = await api.getTmuxLayout(false);
        setLayout(fresh);
        if (selected && !fresh.panes.some((p) => p.pane_id === selected)) select(null);
        if (res.cleanup_ran && !res.cleanup_ok) {
          window.alert(`Window closed, but cleanup failed:\n\n${res.cleanup_output || "(no output)"}`);
        }
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Couldn't remove the session.");
      }
    },
    [pendingRemove, selected, select],
  );
  const createGroup = useCallback(async () => {
    const name = window.prompt("New group name (letters, numbers, _ and - only):")?.trim();
    if (!name) return;
    try {
      await api.createTmuxSession(name);
      await refresh();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Couldn't create the group.");
    }
  }, [refresh]);
  const renameGroup = useCallback(
    async (name: string) => {
      const next = window.prompt(`Rename group "${name}" to:`, name)?.trim();
      if (!next || next === name) return;
      try {
        await api.renameTmuxSession(name, next);
        if (group === name) setGroup(next);
        await refresh();
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Couldn't rename the group.");
      }
    },
    [group, refresh, setGroup],
  );
  const deleteGroup = useCallback(
    async (name: string) => {
      if (!window.confirm(`Delete group "${name}"? This kills its tmux session and every pane in it.`))
        return;
      try {
        await api.deleteTmuxSession(name);
        if (group === name) setGroup(null);
        await refresh();
      } catch (e) {
        window.alert(e instanceof Error ? e.message : "Couldn't delete the group.");
      }
    },
    [group, refresh, setGroup],
  );

  const q = filter.trim().toLowerCase();
  const matches = (w: Win) =>
    !q ||
    (w.window_name || "").toLowerCase().includes(q) ||
    w.session_name.toLowerCase().includes(q) ||
    w.rep.cwd.toLowerCase().includes(q);
  const inGroup = (w: Win) => group === null || w.session_name === group;
  const filtered = wins.filter((w) => matches(w) && inGroup(w));
  const pinned = sortWins(filtered.filter((w) => pins.has(w.key)), "attention");
  const visible = filtered.filter((w) => !isNoise(w) && !pins.has(w.key));
  const noise = filtered.filter((w) => isNoise(w) && !pins.has(w.key));

  // Flat, task-ordered list of panes for the swipeable detail deck (pinned first,
  // matching the list). Windows in the same section keep tmux order; panes within
  // a window are adjacent. Parameterized by group so the section switcher can ask
  // "what would the deck show for section X?" and land on a pane that's actually there.
  const deckPanesFor = (g: string | null) => {
    const inG = (w: Win) => g === null || w.session_name === g;
    const f = wins.filter((w) => matches(w) && inG(w));
    const pin = sortWins(f.filter((w) => pins.has(w.key)), "attention");
    const vis = f.filter((w) => !isNoise(w) && !pins.has(w.key));
    const noi = f.filter((w) => isNoise(w) && !pins.has(w.key));
    const ordered =
      sortMode === "attention"
        ? SECTION_ORDER.flatMap((s) => vis.filter((w) => w.status === s))
        : sortWins(vis, sortMode);
    const all = showNoise ? [...pin, ...ordered, ...noi] : [...pin, ...ordered];
    return all.flatMap((w) => [...w.panes].sort((a, b) => a.pane_index - b.pane_index));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const deckPanes = useMemo(() => deckPanesFor(group), [layout, showNoise, sortMode, q, pins, group]);

  // Switch the deck to another section from its header dropdown. Land on that section's
  // first DECK-visible pane (so it isn't a noise/scratch pane the deck would drop and
  // flash "pane is gone"); if the section has none, fall back to its task list. null
  // widens back to All and keeps the current pane in view.
  const switchGroup = (name: string | null) => {
    setGroup(name);
    if (name === null) return;
    const first = deckPanesFor(name)[0];
    select(first ? first.pane_id : null, true);
  };

  if (!layout) return <div className="empty">Loading tmux…</div>;
  if (!layout.available || layout.panes.length === 0)
    return <div className="empty">{layout.reason ?? "No tmux panes found."}</div>;

  const removeDialog = (
    <RemoveDialog
      pending={pendingRemove}
      onCancel={() => setPendingRemove(null)}
      onConfirm={confirmRemove}
    />
  );

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
      <>
        <PaneDeck
          panes={deckPanes}
          selected={selected}
          onSelect={(id) => select(id, true)}
          onBack={() => select(null)}
          onAction={refresh}
          group={group}
          onSwitchGroup={switchGroup}
          groups={groups.map((g) => g.name)}
          onMoveWindow={deckMove}
          onMoveToNewGroup={deckMoveToNewGroup}
          onLaunchCodex={launchCodexFromPane}
          onRenameWindow={renameWindow}
          onRemoveWindow={removeWindow}
        />
        {removeDialog}
      </>
    );
  }

  const groupNames = groups.map((g) => g.name);
  const row = (w: Win, muted = false) => {
    const sourcePane = w.panes.find((p) => !!p.muse_session_id) ?? null;
    return (
    <TaskRow
      key={w.key}
      win={w}
      onOpen={() => select(w.rep.pane_id)}
      muted={muted}
      pinned={pins.has(w.key)}
      onPin={() => togglePin(w.key)}
      desktop={desktop}
      groups={groupNames}
      onMove={(session) => moveWindow(w.rep.window_id, session)}
      onMoveNew={() => moveToNewGroup(w.rep.window_id)}
      onLaunchCodex={sourcePane ? () => launchCodexFromPane(sourcePane) : undefined}
      onRenameWindow={() => renameWindow(w.rep.window_id, w.window_name)}
      onRemove={() => removeWindow(w.rep.window_id, w.window_name)}
    />
    );
  };

  return (
    <div className="panes-layout">
      <GroupRail
        groups={groups}
        current={group}
        total={wins.length}
        desktop={desktop}
        onSelect={setGroup}
        onCreate={createGroup}
        onRename={renameGroup}
        onDelete={deleteGroup}
        onDropWindow={moveWindow}
      />
      <div className="tasks-page">
      <RestoreBanner onRestored={refresh} />
      <div className="tasks-top">
        <input
          className="tasks-filter"
          type="search"
          placeholder="filter sessions…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <div className="tasks-sort" role="tablist" aria-label="Sort sessions">
          {SORTS.map(({ mode, label }) => (
            <button
              key={mode}
              role="tab"
              aria-selected={sortMode === mode}
              className={`tasks-sort-btn${sortMode === mode ? " active" : ""}`}
              onClick={() => setSort(mode)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {pinned.length > 0 && (
        <section className="tasks-section">
          <h2 className="tasks-heading tasks-pinned">
            ★ Pinned <span className="tasks-count">{pinned.length}</span>
          </h2>
          {pinned.map((w) => row(w))}
        </section>
      )}

      {sortMode === "attention" ? (
        SECTIONS.map(({ status, label }) => {
          const items = sortWins(
            visible.filter((w) => w.status === status),
            "recent",
          );
          if (!items.length) return null;
          return (
            <section key={status} className="tasks-section">
              <h2 className={`tasks-heading tasks-${status}`}>
                {label} <span className="tasks-count">{items.length}</span>
              </h2>
              {items.map((w) => row(w))}
            </section>
          );
        })
      ) : (
        <section className="tasks-section">{sortWins(visible, sortMode).map((w) => row(w))}</section>
      )}

      {q && pinned.length + visible.length + noise.length === 0 && (
        <div className="empty">Nothing matches “{filter}”.</div>
      )}

      {noise.length > 0 && (
        <section className="tasks-section">
          <button className="tasks-noise-toggle" onClick={() => setShowNoise((s) => !s)}>
            {showNoise ? "▾" : "▸"} Scratch &amp; idle shells{" "}
            <span className="tasks-count">{noise.length}</span>
          </button>
          {showNoise && noise.map((w) => row(w, true))}
        </section>
      )}

      <NewWindowLauncher group={group} busy={creating} onLaunch={launch} />
      </div>
      {removeDialog}
    </div>
  );
}

// The group rail (tmux sessions): filter to one group, and — on desktop — a drop
// target for reorganizing (drag a task card onto a group to move its window there).
// Renders a vertical sidebar on desktop, a horizontal chip strip on mobile.
function GroupRail({
  groups,
  current,
  total,
  desktop,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onDropWindow,
}: {
  groups: Group[];
  current: string | null;
  total: number;
  desktop: boolean;
  onSelect: (name: string | null) => void;
  onCreate: () => void;
  onRename: (name: string) => void;
  onDelete: (name: string) => void;
  onDropWindow: (windowId: string, session: string) => void;
}) {
  const [dragOver, setDragOver] = useState<string | null>(null);
  // Only groups accept drops (dropping on "All" has no destination session).
  const dropProps = (name: string) =>
    desktop
      ? {
          onDragOver: (e: React.DragEvent) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            setDragOver(name);
          },
          onDragLeave: () => setDragOver((d) => (d === name ? null : d)),
          onDrop: (e: React.DragEvent) => {
            e.preventDefault();
            const id = e.dataTransfer.getData("text/window-id");
            setDragOver(null);
            if (id) onDropWindow(id, name);
          },
        }
      : {};

  return (
    <aside className={`panes-rail${desktop ? "" : " panes-rail-chips"}`}>
      <button
        className={`panes-rail-item${current === null ? " active" : ""}`}
        onClick={() => onSelect(null)}
      >
        <span className="panes-rail-name">All</span>
        <span className="tasks-count">{total}</span>
      </button>
      {groups.map((g) => (
        <div
          key={g.name}
          className={`panes-rail-item${current === g.name ? " active" : ""}${dragOver === g.name ? " drag-over" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => onSelect(g.name)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") onSelect(g.name);
          }}
          {...dropProps(g.name)}
        >
          <span className={`tab-dot task-${g.status}`} />
          <span className="panes-rail-name">{g.name}</span>
          <span className="tasks-count">{g.windows}</span>
          <button
            className="panes-rail-act"
            title={`Rename group "${g.name}"`}
            onClick={(e) => {
              e.stopPropagation();
              onRename(g.name);
            }}
          >
            ✎
          </button>
          {g.placeholder && (
            <button
              className="panes-rail-act"
              title={`Delete empty group "${g.name}"`}
              onClick={(e) => {
                e.stopPropagation();
                onDelete(g.name);
              }}
            >
              ✕
            </button>
          )}
        </div>
      ))}
      <button className="panes-rail-item panes-rail-new" onClick={onCreate} title="Create a new group">
        + New group
      </button>
    </aside>
  );
}

// Compact relative time since the window's last activity ("now", "4m", "2h", "3d").
function ago(epochSecs: number): string {
  if (!epochSecs) return "";
  const s = Math.max(0, Date.now() / 1000 - epochSecs);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

// Move-to-group menu: pick another group (tmux session) as the destination, or
// "New group…". Shared by the task-list rows (⋯) and the open-pane deck header
// (Move ▾). currentSession is excluded from the target list.
// Confirm dialog for removing (closing) a session. Names the window; when a profile
// claims it (by cwd), offers an opt-in checkbox showing the exact cleanup command.
export function RemoveDialog({
  pending,
  onCancel,
  onConfirm,
}: {
  pending: {
    windowId: string;
    name: string;
    cleanup: { profile: string; command: string } | null;
  } | null;
  onCancel: () => void;
  onConfirm: (cleanup: boolean) => void;
}) {
  const [cleanup, setCleanup] = useState(true);
  useEffect(() => {
    setCleanup(true); // default the checkbox back on each time a new dialog opens
  }, [pending?.windowId]);
  if (!pending) return null;
  return (
    <>
      <div
        className="remove-scrim"
        onPointerDown={(e) => {
          e.preventDefault();
          onCancel();
        }}
      />
      <div className="remove-dialog" role="dialog" aria-label={`Remove ${pending.name}`}>
        <div className="remove-title">
          Remove “{pending.name}”?
        </div>
        <div className="remove-body">Kills the tmux window and the Claude running in it.</div>
        {pending.cleanup && (
          <label className="remove-cleanup">
            <input
              type="checkbox"
              checked={cleanup}
              onChange={(e) => setCleanup(e.target.checked)}
            />
            <span>
              Also run cleanup: <code>{pending.cleanup.command}</code>
            </span>
          </label>
        )}
        <div className="remove-actions">
          <button className="remove-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="remove-confirm"
            onClick={() => onConfirm(pending.cleanup ? cleanup : false)}
          >
            Remove
          </button>
        </div>
      </div>
    </>
  );
}

// Session restore: after a reboot the live tmux is missing the snapshot's Claude sessions.
// When the snapshot reports `offer` (has Claude windows, none currently live), show a banner
// that opens a per-group review and rebuilds the selected groups (resuming each Claude).
export function RestoreBanner({ onRestored }: { onRestored: () => void }) {
  const [snap, setSnap] = useState<LayoutSnapshot | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    api.getTmuxSnapshot().then(setSnap).catch(() => {});
  }, []);

  if (!snap || !snap.offer || dismissed) return null;
  if (snap.ts && localStorage.getItem("restoreDismissed") === snap.ts) return null;

  const missingGroups = snap.groups.filter((g) => g.windows.some((w) => !w.live));
  const winCount = missingGroups.reduce((n, g) => n + g.windows.filter((w) => !w.live).length, 0);

  const dismiss = () => {
    if (snap.ts) localStorage.setItem("restoreDismissed", snap.ts);
    setDismissed(true);
  };
  const openReview = () => {
    setSel(new Set(missingGroups.map((g) => g.name)));
    setReviewing(true);
  };
  const toggle = (name: string) =>
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  const doRestore = async () => {
    if (busy || sel.size === 0) return;
    setBusy(true);
    try {
      await api.restoreTmuxLayout([...sel]);
      onRestored();
      dismiss();
      setReviewing(false);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not restore layout");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="restore-banner">
      <span className="restore-banner-msg">
        ↺ Restore previous layout — {winCount} window{winCount === 1 ? "" : "s"} across{" "}
        {missingGroups.length} group{missingGroups.length === 1 ? "" : "s"} aren&apos;t running.
      </span>
      <span className="restore-banner-actions">
        <button className="restore-review" onClick={openReview}>
          Review
        </button>
        <button className="restore-dismiss" onClick={dismiss}>
          Dismiss
        </button>
      </span>
      {reviewing && (
        <>
          <div
            className="send-menu-scrim"
            onPointerDown={(e) => {
              e.preventDefault();
              setReviewing(false);
            }}
          />
          <div className="send-menu restore-modal" role="dialog" aria-label="Restore layout">
            <div className="task-move-head">Restore previous layout</div>
            {missingGroups.map((g) => {
              const missing = g.windows.filter((w) => !w.live);
              return (
                <label key={g.name} className="restore-group">
                  <input
                    type="checkbox"
                    checked={sel.has(g.name)}
                    onChange={() => toggle(g.name)}
                  />
                  <span className="restore-group-name">{g.name}</span>
                  <span className="restore-group-count">{missing.length}</span>
                  <span className="restore-group-wins">
                    {missing.map((w) => w.window_name).join(", ")}
                  </span>
                </label>
              );
            })}
            <div className="restore-note">
              Windows resume their Claude session. Splits aren&apos;t restored.
            </div>
            <button
              className="send-menu-row restore-go"
              disabled={busy || sel.size === 0}
              onClick={doRestore}
            >
              <span className="send-menu-icon">↺</span>
              <span className="send-menu-label">
                {busy ? "Restoring…" : `Restore selected (${sel.size})`}
              </span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// The "New window" launcher: a split control. The primary button launches the last-used
// built-in provider/profile (default: Claude); the ▾ caret opens built-in providers plus
// ~/.muse/profiles.toml entries. Picking a profile with declared params opens a tiny form
// to collect them; params-less profiles launch immediately. Launches land in the current
// group (handled by the caller). Works on both desktop and mobile.
export function NewWindowLauncher({
  group,
  busy,
  onLaunch,
}: {
  group: string | null;
  busy: boolean;
  onLaunch: (name: string, values: Record<string, string>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<Profile | null>(null); // profile awaiting param input
  const [values, setValues] = useState<Record<string, string>>({});
  const [primary, setPrimary] = useState(() => localStorage.getItem("panesPrimaryProfile") || "Claude");

  const rememberPrimary = (name: string) => {
    setPrimary(name);
    localStorage.setItem("panesPrimaryProfile", name);
  };

  const close = () => {
    setOpen(false);
    setForm(null);
  };

  const openMenu = async () => {
    setOpen(true);
    setError(null);
    setProfiles(null);
    try {
      setProfiles(await api.listProfiles());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load profiles");
    }
  };

  const pick = (p: Profile) => {
    if (p.params.length === 0) {
      close();
      rememberPrimary(p.name);
      onLaunch(p.name, {});
      return;
    }
    const init: Record<string, string> = {};
    for (const param of p.params) init[param.key] = param.default;
    setValues(init);
    setForm(p);
  };

  const submitForm = () => {
    if (!form) return;
    const name = form.name;
    close();
    rememberPrimary(name);
    onLaunch(name, values);
  };

  const builtins = profiles?.filter((p) => p.builtin) ?? [];
  const customs = profiles?.filter((p) => !p.builtin) ?? [];
  const primaryLabel = PROVIDER_LABEL[(builtins.find((p) => p.name === primary)?.provider ?? "").toLowerCase()] || primary;

  return (
    <div className="task-new-wrap">
      <button
        className="task-new"
        onClick={() => {
          rememberPrimary(primary);
          onLaunch(primary, {});
        }}
        disabled={busy}
      >
        <span className="task-new-plus">{busy ? "…" : "+"}</span>
        {busy ? "Starting…" : `New ${primaryLabel}`}
      </button>
      <button
        className="task-new-caret"
        title="Launch a profile"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy}
        onClick={() => (open ? close() : openMenu())}
      >
        ▾
      </button>
      {open && (
        <>
          <div
            className="send-menu-scrim"
            onPointerDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              close();
            }}
          />
          {form ? (
            <div className="send-menu profile-form" role="dialog" aria-label={`Launch ${form.name}`}>
              <div className="task-move-head">{form.name}</div>
              {form.params.map((param, i) => (
                <label key={param.key} className="profile-field">
                  <span className="profile-field-label">{param.prompt}</span>
                  <input
                    className="profile-field-input"
                    autoFocus={i === 0}
                    value={values[param.key] ?? ""}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [param.key]: e.target.value }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        submitForm();
                      } else if (e.key === "Escape") {
                        close();
                      }
                    }}
                  />
                </label>
              ))}
              <button className="send-menu-row profile-launch" onClick={submitForm}>
                <span className="send-menu-icon">▸</span>
                <span className="send-menu-label">Launch{group ? ` in ${group}` : ""}</span>
              </button>
            </div>
          ) : (
            <div className="send-menu task-move-menu" role="menu">
              <div className="task-move-head">New window from…</div>
              {error && <div className="profile-note profile-note-error">{error}</div>}
              {!error && profiles === null && <div className="profile-note">Loading…</div>}
              {builtins.length > 0 && (
                <>
                  <div className="profile-note">Providers</div>
                  {builtins.map((p) => (
                    <button
                      key={p.name}
                      className="send-menu-row"
                      onClick={(e) => {
                        e.stopPropagation();
                        pick(p);
                      }}
                    >
                      <span className="send-menu-icon">{p.params.length ? "…" : "▸"}</span>
                      <span className="send-menu-label">{p.name}</span>
                    </button>
                  ))}
                </>
              )}
              {customs.length > 0 && (
                <>
                  <div className="profile-note">Profiles</div>
                  {customs.map((p) => (
                    <button
                      key={p.name}
                      className="send-menu-row"
                      onClick={(e) => {
                        e.stopPropagation();
                        pick(p);
                      }}
                    >
                      <span className="send-menu-icon">{p.params.length ? "…" : "▸"}</span>
                      <span className="send-menu-label">{p.name}</span>
                    </button>
                  ))}
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function MoveMenu({
  groups,
  currentSession,
  onMove,
  onMoveNew,
  onLaunchCodex,
  onRemove,
  triggerClass = "task-move",
  triggerLabel = "⋯",
  title = "Move to a group",
}: {
  groups: string[];
  currentSession: string;
  onMove: (session: string) => void;
  onMoveNew: () => void;
  onLaunchCodex?: () => void;
  onRemove?: () => void; // remove (close) this window — a danger row under the move options
  triggerClass?: string;
  triggerLabel?: string;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const others = groups.filter((g) => g !== currentSession);
  return (
    <span className="task-move-wrap">
      <button
        className={triggerClass}
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        {triggerLabel}
      </button>
      {open && (
        <>
          <div
            className="send-menu-scrim"
            onPointerDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setOpen(false);
            }}
          />
          <div className="send-menu task-move-menu" role="menu">
            <div className="task-move-head">Window actions</div>
            {others.map((g) => (
              <button
                key={g}
                className="send-menu-row"
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen(false);
                  onMove(g);
                }}
              >
                <span className="send-menu-icon">▸</span>
                <span className="send-menu-label">{g}</span>
              </button>
            ))}
            <button
              className="send-menu-row"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onMoveNew();
              }}
            >
              <span className="send-menu-icon">+</span>
              <span className="send-menu-label">New group…</span>
            </button>
            {onLaunchCodex && (
              <button
                className="send-menu-row"
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen(false);
                  onLaunchCodex();
                }}
              >
                <span className="send-menu-icon">↗</span>
                <span className="send-menu-label">New Codex here</span>
              </button>
            )}
            {onRemove && (
              <button
                className="send-menu-row danger"
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen(false);
                  onRemove();
                }}
              >
                <span className="send-menu-icon">✕</span>
                <span className="send-menu-label">Remove session…</span>
              </button>
            )}
          </div>
        </>
      )}
    </span>
  );
}

export function TaskRow({
  win,
  onOpen,
  muted,
  pinned = false,
  onPin,
  desktop = false,
  groups = [],
  onMove,
  onMoveNew,
  onLaunchCodex,
  onRenameWindow,
  onRemove,
}: {
  win: Win;
  onOpen: () => void;
  muted?: boolean;
  pinned?: boolean;
  onPin?: () => void;
  desktop?: boolean;
  groups?: string[]; // all group (session) names — move targets
  onMove?: (session: string) => void; // move this window into an existing group
  onMoveNew?: () => void; // move this window into a freshly-created group
  onLaunchCodex?: () => void; // start a Codex session here, seeded from this one
  onRenameWindow?: () => void; // rename this window (hold the name / desktop ✎)
  onRemove?: () => void; // remove (close) this window + optional profile cleanup
}) {
  const longPress = useLongPress(() => onRenameWindow?.());
  const queued = win.panes.reduce((n, p) => n + (p.queued ?? 0), 0);
  const icon =
    win.status === "needs_you"
      ? "✋"
      : win.status === "responded"
        ? "↩"
        : win.status === "working"
          ? "⟳"
          : "";
  const canMove = !!onMove && !!win.rep.window_id;
  // A div (not a button) so the pin control can be a real nested button. Desktop:
  // draggable onto a rail group (the drop target reads the window id back).
  return (
    <div
      className={`task-row${muted ? " muted" : ""}`}
      role="button"
      tabIndex={0}
      draggable={desktop && canMove}
      onDragStart={
        desktop && canMove
          ? (e) => {
              e.dataTransfer.setData("text/window-id", win.rep.window_id);
              e.dataTransfer.effectAllowed = "move";
            }
          : undefined
      }
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onOpen();
      }}
    >
      <span className={`task-dot task-${win.status}`} />
      <span className="task-main">
        <span className="task-name-row">
          <span className="task-name" {...(onRenameWindow ? longPress : {})}>
            {win.window_name || `${win.session_name}:${win.window_index}`}
          </span>
          {onRenameWindow && desktop && (
            <button
              className="task-rename"
              title="Rename window"
              onClick={(e) => {
                e.stopPropagation();
                onRenameWindow();
              }}
            >
              ✎
            </button>
          )}
        </span>
        <span className="task-sub">
          {win.rep.attention || win.rep.preview_tail || win.rep.command}
        </span>
      </span>
      <span className="task-meta">
        <span className="task-cmd">{win.rep.command}</span>
        <span className="task-ago">{ago(win.last_activity)}</span>
        {win.panes.length > 1 && <span className="task-panes">▦{win.panes.length}</span>}
        {queued > 0 && (
          <span className="task-queued" title={`${queued} reply(ies) queued for next idle`}>
            ⏳{queued}
          </span>
        )}
        {icon && <span className="task-icon">{icon}</span>}
        {onPin && (
          <button
            className={`task-pin${pinned ? " pinned" : ""}`}
            title={pinned ? "Unpin" : "Pin to top"}
            onClick={(e) => {
              e.stopPropagation();
              onPin();
            }}
          >
            {pinned ? "★" : "☆"}
          </button>
        )}
        {onRemove && !!win.rep.window_id && (
          // Always-visible close, not buried in the ⋯ menu — one tap opens the
          // confirm dialog (RemoveDialog), so it's reachable but never destructive
          // by accident.
          <button
            className="task-close"
            title={`Close session “${win.window_name || win.session_name}”`}
            aria-label="Close session"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
          >
            ✕
          </button>
        )}
        {canMove && (
          <MoveMenu
            groups={groups}
            currentSession={win.session_name}
            onMove={(s) => onMove?.(s)}
            onMoveNew={() => onMoveNew?.()}
            onLaunchCodex={onLaunchCodex}
            onRemove={onRemove}
          />
        )}
      </span>
    </div>
  );
}

// The deck header's section switcher: a chip showing the current group (or "All") that
// opens a dropdown of every section to jump to, plus a ✕ to widen back to All. Lets you
// hop between tmux sessions without going back to the task list.
export function DeckGroupSwitcher({
  group,
  groups,
  onSwitch,
}: {
  group: string | null;
  groups: string[];
  onSwitch: (name: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const pick = (name: string | null) => {
    setOpen(false);
    onSwitch(name);
  };
  return (
    <span className="panes-group-switch">
      <button
        className="panes-group-chip"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title="Switch section"
      >
        ▦ {group ?? "All"} ▾
      </button>
      {group && (
        <button className="panes-group-x" onClick={() => onSwitch(null)} title="Show all sections">
          ✕
        </button>
      )}
      {open && (
        <>
          <div
            className="send-menu-scrim"
            onPointerDown={(e) => {
              e.preventDefault();
              setOpen(false);
            }}
          />
          <div className="send-menu panes-group-menu" role="menu">
            <div className="task-move-head">Switch section</div>
            <button className="send-menu-row" onClick={() => pick(null)}>
              <span className="send-menu-icon">{group === null ? "✓" : "▦"}</span>
              <span className="send-menu-label">All sections</span>
            </button>
            {groups.map((g) => (
              <button key={g} className="send-menu-row" onClick={() => pick(g)}>
                <span className="send-menu-icon">{g === group ? "✓" : "▸"}</span>
                <span className="send-menu-label">{g}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </span>
  );
}

// One window tab in the deck's top bar. A div (not a button) so the ✎ rename can
// nest; long-press the name to rename on touch, ✎ on the active tab for desktop.
function DeckTab({
  active,
  status,
  name,
  count,
  desktop,
  onSelect,
  onRename,
}: {
  active: boolean;
  status: Status;
  name: string;
  count: number;
  desktop: boolean;
  onSelect: () => void;
  onRename: () => void;
}) {
  const longPress = useLongPress(onRename);
  return (
    <div
      className={`panes-tab${active ? " active" : ""}`}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect();
      }}
    >
      <span className={`tab-dot task-${status}`} />
      <span className="panes-tab-name" {...longPress}>
        {name}
      </span>
      {count > 1 && <span className="tab-panes">▦{count}</span>}
      {desktop && active && (
        <button
          className="panes-tab-rename"
          title="Rename window"
          onClick={(e) => {
            e.stopPropagation();
            onRename();
          }}
        >
          ✎
        </button>
      )}
    </div>
  );
}

function PaneDeck({
  panes: incomingPanes,
  selected,
  onSelect,
  onBack,
  onAction,
  group,
  onSwitchGroup,
  groups,
  onMoveWindow,
  onMoveToNewGroup,
  onLaunchCodex,
  onRenameWindow,
  onRemoveWindow,
}: {
  panes: TmuxPane[];
  selected: string;
  onSelect: (id: string) => void;
  onBack: () => void;
  onAction: () => void;
  group: string | null; // active group filter — the deck only holds this group's panes
  onSwitchGroup: (name: string | null) => void; // switch section (null = All) from the header
  groups: string[]; // all group (session) names — move targets for the header menu
  onMoveWindow: (windowId: string, session: string) => void; // recategorize + follow
  onMoveToNewGroup: (windowId: string) => void; // create a group, move, focus it
  onLaunchCodex: (pane: TmuxPane) => void; // branch a pane into a new Codex session
  onRenameWindow: (windowId: string, currentName: string) => void; // rename a window
  onRemoveWindow: (windowId: string, name: string) => void; // remove (close) a window
}) {
  const deckRef = useRef<HTMLDivElement>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const lastIdx = useRef(-1);
  const desktop = useIsDesktop();

  // The deck is a horizontal, index-addressed swipe strip, but the incoming list
  // is attention-sorted and reshuffles every poll (your other sessions flip
  // status constantly). If order changed under the viewport, the scroll handler
  // would "select" whatever pane slid into view — a random jump on any refresh.
  // So freeze the order for the deck's lifetime: keep first-seen order, refresh
  // each pane's DATA in place, append genuinely-new panes, drop closed ones.
  // (Unmounting on Back resets this, so reopening re-snapshots the live order.)
  const orderRef = useRef<string[]>([]);
  const panes = useMemo(() => {
    const byId = new Map(incomingPanes.map((p) => [p.pane_id, p]));
    const kept = orderRef.current.filter((id) => byId.has(id));
    const seen = new Set(kept);
    for (const p of incomingPanes) if (!seen.has(p.pane_id)) kept.push(p.pane_id);
    orderRef.current = kept;
    return kept.map((id) => byId.get(id)!);
  }, [incomingPanes]);
  const idxOf = (id: string) => panes.findIndex((p) => p.pane_id === id);

  // Terminal font size, cycled from the tab bar (persisted). "m" matches the
  // historical 12px; "s" fits more columns, "l" is easier on the eyes.
  const [fontSize, setFontSize] = useState(() => localStorage.getItem("panesFont") || "m");
  const cycleFont = () => {
    const next = fontSize === "s" ? "m" : fontSize === "m" ? "l" : "s";
    setFontSize(next);
    localStorage.setItem("panesFont", next);
  };

  // Reader mode (persisted): Claude panes show the PARSED CONVERSATION (like the
  // drive cockpit's focus view) instead of the raw terminal grid — a terminal is
  // hostile on a phone. Untracked panes always fall back to the terminal.
  // Key is versioned (…v2) so a stale "term" saved during earlier testing is
  // dropped once — everyone re-defaults to the reader (nice) view.
  const [view, setView] = useState<"term" | "reader">(
    () => (localStorage.getItem("panesView.v2") as "term" | "reader") || "reader",
  );
  const toggleView = () => {
    const next = view === "reader" ? "term" : "reader";
    setView(next);
    localStorage.setItem("panesView.v2", next);
  };

  // Full screen: drop the browser chrome via the Fullscreen API while keeping the
  // app layout exactly as-is — same view, more pixels. Works on Android/desktop
  // Chrome; a no-op on iOS Safari (there the installed PWA is the only true full
  // screen). Track the live state so the button reflects it (e.g. Esc exits).
  const [isFull, setIsFull] = useState(false);
  useEffect(() => {
    const sync = () => setIsFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    else document.documentElement.requestFullscreen?.().catch(() => {});
  };


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

  // Desktop keyboard nav (distinct from the phone's swipe): ←/→ move between panes,
  // Esc returns to the task list. This works even while the composer is focused —
  // but only when the caret is already at the matching edge of the text (or the
  // box is empty), so ← still moves the cursor left when there's text to its left.
  // Non-field targets (the reader, the deck) always navigate.
  useEffect(() => {
    // Terminal view owns the keyboard (PaneCard passthrough handles nav + input there);
    // this caret-boundary arrow nav is for the reader/viewer view only.
    if (!desktop || view === "term") return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      const field =
        tag === "INPUT" || tag === "TEXTAREA" ? (t as HTMLTextAreaElement) : null;
      const editableNonField = !field && !!t?.isContentEditable;
      const i = idxOf(selected);
      if (e.key === "ArrowRight") {
        if (editableNonField) return;
        if (field && !(field.selectionStart === field.value.length && field.selectionEnd === field.value.length))
          return; // caret has text to its right — move the cursor, don't navigate
        if (panes[i + 1]) {
          e.preventDefault();
          onSelect(panes[i + 1].pane_id);
        }
      } else if (e.key === "ArrowLeft") {
        if (editableNonField) return;
        if (field && !(field.selectionStart === 0 && field.selectionEnd === 0)) return;
        if (panes[i - 1]) {
          e.preventDefault();
          onSelect(panes[i - 1].pane_id);
        }
      } else if (e.key === "Escape" && !field && !editableNonField) {
        e.preventDefault();
        onBack();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop, view, panes, selected, onSelect, onBack]);

  // Step the active selection by ±1 pane — the terminal-mode Alt+←/→ nav carveout, which
  // PaneCard's passthrough calls (it can't see the deck order).
  const stepPane = useCallback(
    (dir: -1 | 1) => {
      const next = panes[idxOf(selected) + dir];
      if (next) onSelect(next.pane_id);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [panes, selected, onSelect],
  );

  // Full tab menu (one per window, deck order), each with a status dot so it shows
  // more than the name. Tapping jumps; the active tab tracks swipes. A window split
  // into multiple panes escalates its dot to the most-urgent pane and shows a count.
  const tabMap = new Map<string, { index: number; name: string; status: Status; count: number }>();
  panes.forEach((p, i) => {
    const key = `${p.session_name}:${p.window_index}`;
    const ex = tabMap.get(key);
    if (!ex) tabMap.set(key, { index: i, name: p.window_name || key, status: p.status, count: 1 });
    else {
      ex.count += 1;
      if (RANK[p.status] < RANK[ex.status]) ex.status = p.status; // escalate
    }
  });
  const tabs = [...tabMap.entries()].map(([key, t]) => ({ key, ...t }));
  const current = panes[Math.max(0, idxOf(selected))];
  const currentWin = current && `${current.session_name}:${current.window_index}`;

  // Keep the active tab visible: with 13+ windows the tab strip scrolls, and a
  // swipe/back-navigation would otherwise leave the highlight off-screen.
  useEffect(() => {
    tabsRef.current
      ?.querySelector(".panes-tab.active")
      ?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }, [currentWin]);
  // Panes of the window we're currently on — surfaced as a sub-selector so split
  // windows (e.g. a claude pane beside a bash pane) are directly navigable, not just
  // reachable by a blind swipe.
  const currentWinPanes = panes.filter(
    (p) => `${p.session_name}:${p.window_index}` === currentWin,
  );

  return (
    <div className="panes-detail">
      <div className="panes-tabbar">
        <button className="drive-back" onClick={onBack} title="Back to tasks">
          ‹
        </button>
        <DeckGroupSwitcher group={group} groups={groups} onSwitch={onSwitchGroup} />
        <div className="panes-tabs" ref={tabsRef}>
          {tabs.map((t) => (
            <DeckTab
              key={t.key}
              active={t.key === currentWin}
              status={t.status}
              name={t.name}
              count={t.count}
              desktop={desktop}
              onSelect={() => onSelect(panes[t.index].pane_id)}
              onRename={() =>
                onRenameWindow(panes[t.index].window_id, panes[t.index].window_name)
              }
            />
          ))}
        </div>
        <button
          className={`panes-font-btn${view === "reader" ? " active" : ""}`}
          onClick={toggleView}
          title={
            view === "reader"
              ? "Reader mode: parsed conversation for Claude panes (tap for raw terminal)"
              : "Terminal mode: raw screen (tap for reader mode)"
          }
        >
          {view === "reader" ? "💬" : "▤"}
        </button>
        <button
          className="panes-font-btn"
          onClick={cycleFont}
          title={`Terminal font size: ${fontSize.toUpperCase()} (tap to cycle)`}
        >
          A<span className="panes-font-size">{fontSize.toUpperCase()}</span>
        </button>
        <button
          className={`panes-font-btn${isFull ? " active" : ""}`}
          onClick={toggleFullscreen}
          title="Full screen — drop the browser chrome (same view, more screen)"
        >
          ⛶
        </button>
      </div>

      {currentWinPanes.length > 1 && (
        <div className="panes-subtabs">
          {currentWinPanes.map((p) => (
            <button
              key={p.pane_id}
              className={`panes-subtab${p.pane_id === selected ? " active" : ""}`}
              onClick={() => onSelect(p.pane_id)}
            >
              <span className={`tab-dot task-${p.status}`} />
              {p.command}
              <span className="subtab-idx">·{p.pane_index}</span>
            </button>
          ))}
        </div>
      )}

      <div className={`panes-deck font-${fontSize}`} ref={deckRef} onScroll={onScroll}>
        {panes.map((p, i) => (
          <PaneCard
            key={p.pane_id}
            pane={p}
            onAction={onAction}
            active={p.pane_id === selected}
            // Fetch live screens only for the pane on screen and its swipe
            // neighbors — the layout poll no longer ships screen text.
            near={Math.abs(i - Math.max(0, idxOf(selected))) <= 1}
            view={view}
            groups={groups}
            onMove={(session) => onMoveWindow(p.window_id, session)}
            onMoveNew={() => onMoveToNewGroup(p.window_id)}
            onLaunchCodex={p.muse_session_id ? () => onLaunchCodex(p) : undefined}
            onRemove={() => onRemoveWindow(p.window_id, p.window_name)}
            onStepPane={stepPane}
            onExit={onBack}
          />
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

// Terminal key bar (two rows, aligned into a grid). Each cell either sends a key
// (`k`), toggles a sticky modifier (`mod`), dismisses the on-screen keyboard
// (`kbd`), or is an alignment spacer (`null`). The layout keeps the arrow keys in
// an inverted-T and Home/End over ←/→, PgUp over PgDn.
type KeyCell =
  | { label: string; k: string }
  | { label: string; mod: "ctrl" | "alt" | "fn" }
  | null;
const KEYBAR: KeyCell[] = [
  // Two rows of 8 on an 8-col grid — columns align ↑ over ↓, home/end over ←/→,
  // pgup over pgdn, and fn sits under - (between alt and ←). No gaps.
  // row 1
  { label: "esc", k: "escape" },
  { label: "/", k: "/" },
  { label: "|", k: "|" },
  { label: "⏎", k: "enter" },
  { label: "home", k: "home" },
  { label: "↑", k: "up" },
  { label: "end", k: "end" },
  { label: "pgup", k: "pageup" },
  // row 2
  { label: "tab", k: "tab" },
  { label: "ctrl", mod: "ctrl" },
  { label: "alt", mod: "alt" },
  { label: "fn", mod: "fn" },
  { label: "←", k: "left" },
  { label: "↓", k: "down" },
  { label: "→", k: "right" },
  { label: "pgdn", k: "pagedown" },
];


function PaneCard({
  pane,
  onAction,
  active = false,
  near = false,
  view = "term",
  groups = [],
  onMove,
  onMoveNew,
  onLaunchCodex,
  onRemove,
  onStepPane,
  onExit,
}: {
  pane: TmuxPane;
  onAction: () => void;
  active?: boolean;
  near?: boolean; // adjacent in the swipe deck — prefetch so a swipe lands warm
  view?: "term" | "reader";
  groups?: string[]; // all group (session) names — move targets (desktop header menu)
  onMove?: (session: string) => void; // recategorize this window into an existing group
  onMoveNew?: () => void; // recategorize into a freshly-created group
  onLaunchCodex?: () => void; // start a Codex session here, seeded from this one
  onRemove?: () => void; // remove (close) this window from the header menu
  onStepPane?: (dir: -1 | 1) => void; // terminal passthrough: Alt+←/→ step panes
  onExit?: () => void; // terminal passthrough: Alt+Esc back to the task list
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const desktop = useIsDesktop();
  const untrackedRef = useRef<HTMLTextAreaElement>(null);
  const cwdShort = pane.cwd.split("/").slice(-1)[0] || pane.cwd;

  // The layout poll is slim (no screen text) — each visible/adjacent card polls
  // its own live screen. That capture also carries the freshest menu + mode, so
  // it keeps running in reader mode too (chips must reflect the real pane).
  const [screen, setScreen] = useState<PaneScreen | null>(null);
  usePolling(
    async () => setScreen(await api.getPaneScreen(pane.pane_id)),
    2000,
    active || near,
  );
  const screenText = screen?.text || pane.preview || "";
  const options = screen ? screen.options : pane.options;
  const mode = screen?.mode ?? pane.mode;
  const caps = pane.capabilities;
  const providerLabel = pane.provider ? (PROVIDER_LABEL[pane.provider] ?? pane.provider) : null;
  const showRightRail =
    (mode != null && caps.mode_switch) ||
    pane.context_pct != null ||
    pane.queued > 0 ||
    (!!pane.muse_session_id && caps.drive);
  // Parse ANSI once per screen update (not on every keystroke in the composer).
  const segments = useMemo(() => parseAnsi(screenText), [screenText]);

  // Reader mode: the parsed conversation instead of the terminal grid. Threads
  // are heavier than screens, so only the ACTIVE card polls (the server window-
  // loads just the tail; parse is mtime-cached). History accumulates as you
  // scroll up, so the whole session is reachable without leaving the deck.
  // Data layer (poll/merge/prepend) lives in useReaderThread — unit-tested.
  const sid = pane.muse_session_id;
  const showReader = view === "reader" && !!sid && caps.reader;
  const { reader, loadEarlier, pullingEarlier } = useReaderThread(sid, showReader && active);

  // Rich pending options (full prompt + descriptions + free-text) from the
  // thread-aware endpoint — the screen-parsed chips stay as the fallback for
  // untracked panes and parse gaps. Active card only: one 1.8s poll, not ×8.
  const { pending, sending, select } = usePendingOptions(
    sid ?? "",
    !!sid && active && caps.rich_reply,
  );
  // Bumped when ReplyBox queues so the chips below refresh instantly.
  const [queueBump, setQueueBump] = useState(0);
  // One-line status banner — only when it says something actionable (a pending
  // prompt or an in-progress turn); idle needs no banner, the composer is right
  // there. Pure derivation, no new poll.
  const working = pane.status === "working";
  const statusCls = pending ? "wait" : "busy";
  const statusText = pending
    ? "Needs your input — choose below"
    : `${providerLabel ?? "Agent"} is working — queue a reply or interrupt`;
  // Distance-from-bottom captured before a prepend, to restore the exact reading
  // position after the earlier items land above the viewport.
  const fromBottom = useRef<number | null>(null);

  const pullEarlier = useCallback(async () => {
    const el = screenRef.current;
    fromBottom.current = el ? el.scrollHeight - el.scrollTop : null;
    if (!(await loadEarlier())) fromBottom.current = null;
  }, [loadEarlier]);

  // After a prepend, put the viewport back exactly where the user was reading
  // (same distance from the bottom), before the browser paints.
  useLayoutEffect(() => {
    const el = screenRef.current;
    if (el && fromBottom.current != null) {
      el.scrollTop = el.scrollHeight - fromBottom.current;
      fromBottom.current = null;
    }
  }, [reader?.start]);

  // Scroll (terminal or reader): default to the bottom (the newest), but only
  // auto-follow new content when the user is already near the bottom — so
  // scrolling UP to read history isn't yanked back on the next poll.
  const screenRef = useRef<HTMLElement | null>(null);
  const setScrollEl = useCallback((el: HTMLElement | null) => {
    screenRef.current = el;
  }, []);
  const stick = useRef(true);
  // Scrolled up off the bottom → show the jump-to-latest pill; `fresh` marks
  // that new activity landed below while the user was reading history.
  const [unstuck, setUnstuck] = useState(false);
  const [fresh, setFresh] = useState(false);
  const toBottom = useCallback(() => {
    const pin = () => {
      const el = screenRef.current;
      if (el && stick.current) el.scrollTop = el.scrollHeight;
    };
    requestAnimationFrame(pin);
    // Reader content keeps growing after the first frame (markdown, code
    // highlighting, collapsibles measuring themselves) — one rAF lands on the
    // TOP of a still-short page. Re-pin through the late layout passes; each
    // pass re-checks stick so a user scrolling up is never yanked back.
    window.setTimeout(pin, 120);
    window.setTimeout(pin, 400);
    window.setTimeout(pin, 900);
  }, []);
  const jumpToLatest = useCallback(() => {
    stick.current = true;
    setUnstuck(false);
    setFresh(false);
    toBottom();
  }, [toBottom]);
  const onScreenScroll = () => {
    const el = screenRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    setUnstuck(!stick.current); // React bails on same-value sets — no churn
    if (stick.current) setFresh(false);
    // Nearing the top of the reader → pull in earlier history.
    if (showReader && el.scrollTop < 400) pullEarlier();
  };
  // Follow new content when already at the bottom. `reader` identity only
  // changes when the tail signature changed (new message or a tool result
  // landing), so this can't fire on no-op polls — meaning if we're unstuck and
  // this runs, it's genuinely new activity below.
  useEffect(() => {
    if (stick.current) toBottom();
    else setFresh(true);
  }, [screenText, reader, toBottom]);
  // Switching term ⇄ reader swaps the scroll container — land on the newest.
  useEffect(() => {
    stick.current = true;
    setUnstuck(false);
    setFresh(false);
    toBottom();
  }, [showReader, toBottom]);
  // Jump to the bottom whenever this card becomes the active tab (open/switch/swipe).
  useEffect(() => {
    if (active) {
      stick.current = true;
      setUnstuck(false);
      setFresh(false);
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

  // Show the effect of an action right away instead of waiting out the 2s poll.
  const refetchScreen = useCallback(async () => {
    try {
      setScreen(await api.getPaneScreen(pane.pane_id));
    } catch {
      /* next poll covers it */
    }
  }, [pane.pane_id]);

  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      await api.sendToPane(pane.pane_id, t);
      setText("");
      onAction();
      refetchScreen();
    } finally {
      setBusy(false);
    }
  };

  // "/" autocomplete for the untracked-pane composer (tracked panes get it via
  // ReplyBox). Harmless to always call — gated off when a tracked ReplyBox owns
  // the composer instead.
  const untrackedSlash = useSlashMenu({
    paneId: pane.pane_id,
    text,
    setText,
    enabled: !caps.rich_reply && caps.slash_commands,
  });

  // Desktop keyboard niceties for the untracked composer (tracked panes get these
  // via ReplyBox): start-typing-anywhere focuses it, and it grows multi-line.
  const appendText = useCallback((ch: string) => setText((t) => t + ch), []);
  // In terminal view, typing passes through to the session (below) rather than focusing
  // the composer — so only capture-to-focus in the reader/viewer view.
  useTypeToFocus(untrackedRef, appendText, desktop && active && !sid && view !== "term");
  useEffect(() => {
    const el = untrackedRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  const key = async (k: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await api.sendPaneKey(pane.pane_id, k);
      onAction();
      refetchScreen();
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
      refetchScreen();
    } finally {
      setBusy(false);
    }
  };

  // Sticky modifiers (ctrl / alt / fn) for the key bar. Tapping one arms it; the
  // next key — whether a bar key or a character typed on the on-screen keyboard —
  // is sent combined, then the modifiers clear. ctrl→C-, alt→M-, fn+digit→F-key.
  const [mods, setMods] = useState<Set<string>>(() => new Set());
  const clearMods = useCallback(() => setMods((m) => (m.size ? new Set() : m)), []);
  const toggleMod = (name: string) =>
    setMods((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  const sendKey = useCallback(
    (base: string) => {
      let composed: string;
      if (mods.has("fn") && /^[0-9]$/.test(base)) {
        composed = "f" + (base === "0" ? "10" : base); // FN+digit → F-key
      } else {
        composed = (mods.has("ctrl") ? "c-" : "") + (mods.has("alt") ? "m-" : "") + base;
      }
      key(composed);
      clearMods();
    },
    [mods, key, clearMods],
  );
  // While a modifier is armed, capture the very next keystroke anywhere (the
  // focused composer included) and send it combined instead of typing it. A ref
  // keeps the listener stable so it isn't torn down every render.
  const sendKeyRef = useRef(sendKey);
  sendKeyRef.current = sendKey;
  const armed = mods.size > 0;
  useEffect(() => {
    if (!armed) return;
    const onKey = (e: KeyboardEvent) => {
      if (["Shift", "Control", "Alt", "Meta", "CapsLock", "Fn"].includes(e.key)) return;
      const base = eventKeyToName(e.key);
      if (base == null) {
        clearMods();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      sendKeyRef.current(base);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [armed, clearMods]);
  // Run key-bar actions on pointer-DOWN with preventDefault, so the button never
  // takes focus off the composer — otherwise every tap dismisses the on-screen
  // keyboard. preventDefault stops the focus shift; the action still runs.
  const onKeyDown = (fn: () => void) => (e: React.PointerEvent) => {
    e.preventDefault();
    fn();
  };

  // --- Desktop terminal passthrough -------------------------------------------------
  // When this is the active card in terminal view and the composer isn't focused,
  // keystrokes go straight to the pane (classifyKey decides what). Printables coalesce
  // into one send; every send serializes through a promise chain so fast typing isn't
  // dropped (the key-bar's key() drops while busy) or reordered.
  const [composerFocused, setComposerFocused] = useState(false);
  const ptEnabled = desktop && active && view === "term";
  const sendChain = useRef<Promise<unknown>>(Promise.resolve());
  const charBuf = useRef("");
  const flushTimer = useRef<number | undefined>(undefined);
  const refreshTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!ptEnabled) return;
    const paneId = pane.pane_id;
    const scheduleRefresh = () => {
      window.clearTimeout(refreshTimer.current);
      refreshTimer.current = window.setTimeout(() => {
        onAction();
        refetchScreen();
      }, 80);
    };
    const flushBuf = () => {
      window.clearTimeout(flushTimer.current);
      const t = charBuf.current;
      charBuf.current = "";
      if (!t) return;
      sendChain.current = sendChain.current
        .then(() => api.sendToPane(paneId, t, false))
        .catch(() => {});
      scheduleRefresh();
    };
    const sendComposed = (k: string) => {
      flushBuf(); // keep order: any buffered printables go first
      sendChain.current = sendChain.current
        .then(() => api.sendPaneKey(paneId, k))
        .catch(() => {});
      scheduleRefresh();
    };
    const onKey = (e: KeyboardEvent) => {
      if (mods.size > 0) return; // a sticky key-bar modifier is armed — it owns the next key
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return; // composing
      const a = classifyKey(e);
      if (a.kind === "ignore") return;
      e.preventDefault();
      if (a.kind === "char") {
        charBuf.current += a.ch;
        window.clearTimeout(flushTimer.current);
        flushTimer.current = window.setTimeout(flushBuf, 12);
      } else if (a.kind === "key") {
        sendComposed(a.key);
      } else if (a.kind === "prevPane") {
        onStepPane?.(-1);
      } else if (a.kind === "nextPane") {
        onStepPane?.(1);
      } else if (a.kind === "back") {
        onExit?.();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      flushBuf(); // don't strand buffered characters when the listener tears down
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ptEnabled, pane.pane_id, mods, onStepPane, onExit, onAction, refetchScreen]);

  // Track composer focus so the "live" affordance reflects whether keys are being captured.
  useEffect(() => {
    if (!ptEnabled) {
      setComposerFocused(false);
      return;
    }
    const check = () => {
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      setComposerFocused(tag === "INPUT" || tag === "TEXTAREA" || !!el?.isContentEditable);
    };
    check();
    document.addEventListener("focusin", check);
    document.addEventListener("focusout", check);
    return () => {
      document.removeEventListener("focusin", check);
      document.removeEventListener("focusout", check);
    };
  }, [ptEnabled]);
  const ptLive = ptEnabled && !composerFocused;

  return (
    <div className="pane-card">
      <div className="pane-card-head">
        <span className="pane-card-loc">
          {pane.session_name}:{pane.window_index}.{pane.pane_index}
        </span>
        <span className="pane-card-cmd">{pane.command}</span>
        {providerLabel && <span className={`provider-badge provider-${pane.provider}`}>{providerLabel}</span>}
        <span className="pane-card-cwd" title={pane.cwd}>
          {cwdShort}
        </span>
        {showRightRail && (
          <span className="pane-card-right">
            {mode && caps.mode_switch && (
              <button
                className={`pane-card-mode mode-${mode}`}
                disabled={busy}
                onClick={cycleMode}
                title="Tap to cycle permission mode (Shift+Tab)"
              >
                {MODE_LABEL[mode] ?? mode}
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
            {pane.queued > 0 && (
              <span className="task-queued" title="Replies queued for next idle (manage in ▸ drive)">
                ⏳{pane.queued}
              </span>
            )}
            {pane.muse_session_id && caps.drive && (
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
        {desktop && !!pane.window_id && onMove && onMoveNew && (
          // Recategorize the window you're looking at without leaving the deck.
          <MoveMenu
            groups={groups}
            currentSession={pane.session_name}
            onMove={onMove}
            onMoveNew={onMoveNew}
            onLaunchCodex={onLaunchCodex}
            onRemove={onRemove}
            triggerClass="pane-card-move"
            triggerLabel="Move ▾"
            title="Move this window to a group"
          />
        )}
      </div>

      <div className="pane-card-body">
      {showReader ? (
        <div className="pane-card-reader" ref={setScrollEl} onScroll={onScreenScroll}>
          {reader ? (
            reader.items.length === 0 ? (
              <div className="pane-card-reader-empty">No messages yet.</div>
            ) : (
            <>
              {reader.start > 0 && (
                <button
                  className="pane-card-reader-more"
                  disabled={pullingEarlier}
                  onClick={pullEarlier}
                >
                  {pullingEarlier ? "…loading earlier" : `↑ ${reader.start} earlier messages`}
                </button>
              )}
              <ConversationView
                items={reader.items}
                cwd={reader.cwd}
                model={null}
                selectedToolId={null}
                onSelectTool={() => {}}
                registerToolRef={() => {}}
                bookmarks={{}}
                onSaveBookmark={() => {}}
                onRemoveBookmark={() => {}}
                compact
                focus
              />
            </>
            )
          ) : (
            <div className="pane-card-reader-empty">
              {active ? "loading conversation…" : pane.preview_tail || "…"}
            </div>
          )}
        </div>
      ) : (
        <pre
          className={`pane-card-screen${ptLive ? " live" : ""}`}
          ref={setScrollEl}
          onScroll={onScreenScroll}
        >
          {screenText
            ? segments.map((seg, i) => (
                <span key={i} style={seg.style}>
                  {seg.text}
                </span>
              ))
            : screen
              ? "(empty)"
              : pane.preview_tail || "…"}
        </pre>
      )}
      {unstuck && (
        <button
          className={`pane-card-jump${fresh ? " fresh" : ""}`}
          onClick={jumpToLatest}
          title="Jump to the newest output"
        >
          ↓ latest
        </button>
      )}
      </div>

      {ptLive && (
        <div className="pane-card-termhint">
          ⌨ keys → session · Alt+←/→ panes · Alt+Esc back · click box to compose
        </div>
      )}

      {sid && caps.rich_reply && (pending || working) && (
        <div className={`drive-status drive-status-${statusCls}`}>
          <span className="drive-status-dot" />
          {statusText}
        </div>
      )}

      {pending ? (
        // Thread-aware picker: full prompt, per-option descriptions, free-text.
        <OptionPicker
          pending={pending}
          sending={sending}
          onSelect={(id, ft) =>
            select(id, ft).then(() => {
              refetchScreen();
              onAction();
            })
          }
        />
      ) : (
        options.length > 0 && (
          <div className="pane-card-options">
            {options.map((o) => (
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
        )
      )}

      {sid && caps.queue_replies && <QueueChips sessionId={sid} refreshKey={queueBump} enabled={active} />}

      <div className="pane-keybar">
        {KEYBAR.map((cell, i) => {
          if (cell === null) return <span key={i} className="keybar-spacer" aria-hidden />;
          if ("mod" in cell)
            return (
              <button
                key={i}
                className={`keybar-key keybar-mod${mods.has(cell.mod) ? " armed" : ""}`}
                onPointerDown={onKeyDown(() => toggleMod(cell.mod))}
                title={`${cell.label.toUpperCase()} — sticky; applies to the next key`}
              >
                {cell.label}
              </button>
            );
          return (
            <button
              key={i}
              className="keybar-key"
              disabled={busy}
              onPointerDown={onKeyDown(() => sendKey(cell.k))}
              title={`Send ${cell.label}`}
            >
              {cell.label}
            </button>
          );
        })}
      </div>

      {sid && caps.session_reply ? (
        // Tracked session pane: reply via session id so the backend re-resolves
        // the live tmux pane. Claude adds queue/pending semantics on top.
        <ReplyBox
          sessionId={sid}
          hasPane
          busy={working && caps.queue_replies}
          variant="cockpit"
          slashPaneId={caps.slash_commands ? pane.pane_id : undefined}
          // In terminal view, typing passes through to the session (not the composer).
          captureTyping={active && view !== "term"}
          // The key row below already has ⎋ — don't duplicate the interrupt.
          showInterrupt={false}
          onQueued={() => {
            setQueueBump((n) => n + 1);
            onAction();
          }}
          onSent={() => {
            onAction();
            refetchScreen();
          }}
        />
      ) : (
        <div className="pane-card-compose">
          {untrackedSlash.menu}
          <textarea
            ref={untrackedRef}
            className="reply-input"
            placeholder={`reply to ${pane.window_name || pane.command}…`}
            value={text}
            rows={1}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (untrackedSlash.onKeyDown(e)) {
                e.preventDefault();
                return;
              }
              // Desktop: Enter sends, Shift+Enter newline. Touch: Enter = newline.
              if (desktop && e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button
            className="action-btn primary reply-send-icon"
            disabled={busy || !text.trim()}
            aria-label="Send"
            onClick={send}
          >
            ➤
          </button>
        </div>
      )}
    </div>
  );
}
