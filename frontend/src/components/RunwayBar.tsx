import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { RunwayResponse, RunwayWindow } from "../api/types";
import { usePolling } from "../hooks/usePolling";

function fmtDur(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.round((secs % 3600) / 60);
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}` : `${m}m`;
}

function level(w: RunwayWindow, exhaust: boolean): "ok" | "warn" | "over" {
  if (w.pct_used != null && w.pct_used >= 1) return "over";
  // Ahead of pace or projected to hit the wall before the reset → warn.
  if (exhaust || (w.pct_used != null && w.pct_used > w.pct_elapsed + 0.15)) return "warn";
  return "ok";
}

const SOURCE_HINT: Record<string, string> = {
  observed: "ceiling calibrated from your last observed limit hit",
  configured: "ceiling from MUSE_LIMIT_*_USD",
  none: "no $ cap on a subscription plan — muse learns the ceiling when a limit banner appears",
};

function Seg({ w, exhaust, name }: { w: RunwayWindow; exhaust: boolean; name: string }) {
  const title = `${w.label} — ${SOURCE_HINT[w.budget_source] ?? ""}`;
  // Subscription plans have no published $ caps: without a configured/observed
  // ceiling there is no bar to fill — just report spend honestly.
  if (w.budget_usd == null) {
    return (
      <div className="runway-seg runway-ok" title={title}>
        <span className="runway-name">{name}</span>
        <span className="runway-nums">${w.cost_usd.toFixed(0)}</span>
        <span className="runway-reset">↻{fmtDur(w.remaining_seconds)}</span>
      </div>
    );
  }
  const lvl = level(w, exhaust);
  const pct = Math.min(1, w.pct_used ?? 0);
  return (
    <div className={`runway-seg runway-${lvl}`} title={title}>
      <span className="runway-name">{name}</span>
      <span className="runway-track">
        <span className="runway-fill" style={{ width: `${pct * 100}%` }} />
        {/* time-progress notch: spend left of the notch = under pace */}
        <span className="runway-pace" style={{ left: `${w.pct_elapsed * 100}%` }} />
      </span>
      <span className="runway-nums">
        ${w.cost_usd.toFixed(0)}/{w.budget_source === "observed" ? "~" : ""}$
        {w.budget_usd.toFixed(0)}
      </span>
      <span className="runway-reset">↻{fmtDur(w.remaining_seconds)}</span>
    </div>
  );
}

/** One-line budget cockpit: spend vs the plan's 5h + weekly windows, with a
 * pace notch and a projected time-to-limit warning. Polls every 30s (the server
 * side is TTL-cached over the mtime-cached usage scan). */
export default function RunwayBar() {
  const [runway, setRunway] = useState<RunwayResponse | null>(null);
  const refresh = useCallback(async () => setRunway(await api.getRunway()), []);
  usePolling(refresh, 30000);

  if (!runway) return null;
  const exhaust = runway.exhaust_before_reset;
  const eta =
    exhaust && runway.projected_exhaust_at
      ? Math.max(0, (new Date(runway.projected_exhaust_at).getTime() - Date.now()) / 1000)
      : null;

  return (
    <Link to="/stats" className="runway-bar" title={runway.plan_label ?? "usage windows"}>
      <Seg w={runway.five_hour} exhaust={exhaust} name="5h" />
      <Seg w={runway.week} exhaust={false} name="wk" />
      {runway.burn_usd_per_hour > 0 && (
        <span className={`runway-burn${exhaust ? " runway-burn-hot" : ""}`}>
          {exhaust && eta != null
            ? eta <= 0
              ? "⚠ limit hit"
              : `⚠ limit in ~${fmtDur(eta)}`
            : `$${runway.burn_usd_per_hour.toFixed(0)}/h`}
        </span>
      )}
    </Link>
  );
}
