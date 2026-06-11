/** ⌘K command registry. Kept as data so the palette stays a dumb renderer. */

export interface Command {
  id: string;
  label: string;
  hint?: string;
  /** Navigate target, or a special action handled by the palette. */
  to?: string;
  action?: "theme" | "note" | "launch";
}

export const COMMANDS: Command[] = [
  { id: "nav-sessions", label: "Go to Sessions", to: "/" },
  { id: "nav-board", label: "Go to Monitor", to: "/board" },
  { id: "nav-journal", label: "Go to Journal", to: "/journal" },
  { id: "nav-files", label: "Go to Files", to: "/files" },
  { id: "nav-investigations", label: "Go to Investigations", to: "/investigations" },
  { id: "nav-stats", label: "Go to Stats", to: "/stats" },
  { id: "nav-autopilot", label: "Go to Autopilot", to: "/autopilot" },
  { id: "nav-alerts", label: "Go to Alerts", to: "/alerts" },
  { id: "nav-compare", label: "Compare sessions…", to: "/compare" },
  { id: "new-session", label: "New session…", hint: "launch Claude Code", action: "launch" },
  { id: "new-note", label: "New note", hint: "quick worklog entry", action: "note" },
  { id: "theme", label: "Toggle theme", hint: "light ↔ dark", action: "theme" },
];

/** Tiny subsequence scorer: every query char must appear in order; earlier and
 * denser matches score higher. Returns -1 for no match. */
export function fuzzyScore(query: string, target: string): number {
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  if (!q) return 0;
  let score = 0;
  let ti = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found === -1) return -1;
    score += found === ti ? 2 : 1; // consecutive chars beat scattered ones
    ti = found + 1;
  }
  return score - t.length / 100;
}

export function matchCommands(query: string): Command[] {
  return COMMANDS.map((c) => ({ c, s: fuzzyScore(query, c.label) }))
    .filter((x) => x.s >= 0)
    .sort((a, b) => b.s - a.s)
    .map((x) => x.c);
}
