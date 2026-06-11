import type { SessionSummary } from "../api/types";

const LABEL: Record<SessionSummary["state"], string> = {
  live: "● LIVE",
  waiting: "◐ WAITING",
  stopped: "○ STOPPED",
};

/** Colored state pill: live (red, blinking), waiting (amber), stopped (grey). */
export default function StateBadge({ state }: { state: SessionSummary["state"] }) {
  return <span className={`state-badge state-${state}`}>{LABEL[state]}</span>;
}
