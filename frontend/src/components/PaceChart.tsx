import type { WindowStat } from "../api/types";
import { formatDuration, formatUSD } from "../util/format";

const W = 540;
const H = 170;
const PAD = { l: 44, r: 12, t: 12, b: 22 };
const INNER_W = W - PAD.l - PAD.r;
const INNER_H = H - PAD.t - PAD.b;

/**
 * Cumulative spend over a usage window, on a fixed time axis (0 → window).
 * - blue area: cumulative usage up to "now"
 * - vertical "NOW" line: how far through the window's time you are
 * - dashed diagonal: on-budget pace (only when a budget is configured)
 * Comparing where the usage curve sits vs the NOW line / pace line shows
 * whether you're burning faster or slower than the window allows.
 */
export default function PaceChart({ w }: { w: WindowStat }) {
  const win = w.window_seconds || 1;
  const elapsed = Math.min(w.elapsed_seconds, win);
  const total = w.cost_usd;
  const budget = w.budget_usd ?? null;
  const yMax = Math.max(budget ?? 0, total, 0.01) * 1.12;

  const sx = (s: number) => PAD.l + (s / win) * INNER_W;
  const sy = (c: number) => PAD.t + INNER_H - (c / yMax) * INNER_H;

  // Cumulative usage points, clipped to "now".
  const pts: Array<[number, number]> = [[0, 0]];
  let cum = 0;
  for (const b of w.buckets) {
    if (b.offset_seconds > elapsed) break;
    cum += b.cost_usd;
    pts.push([Math.min(b.offset_seconds + w.bucket_seconds, elapsed), cum]);
  }
  pts.push([elapsed, total]);

  const line = pts.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ");
  const area = `${sx(0)},${sy(0)} ${line} ${sx(elapsed)},${sy(0)}`;

  const timePct = Math.round((elapsed / win) * 100);
  const usagePct = budget ? Math.round((total / budget) * 100) : null;

  return (
    <svg className="pace-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      {/* y grid: 0, budget (or max) */}
      <line x1={PAD.l} y1={sy(0)} x2={W - PAD.r} y2={sy(0)} className="pc-axis" />
      {budget != null && (
        <>
          <line x1={PAD.l} y1={sy(budget)} x2={W - PAD.r} y2={sy(budget)} className="pc-budget" />
          <text x={PAD.l - 6} y={sy(budget) + 4} className="pc-ylabel" textAnchor="end">
            {formatUSD(budget)}
          </text>
          {/* on-pace diagonal: 0 → budget across the full window */}
          <line x1={sx(0)} y1={sy(0)} x2={sx(win)} y2={sy(budget)} className="pc-pace" />
        </>
      )}
      <text x={PAD.l - 6} y={sy(0) + 4} className="pc-ylabel" textAnchor="end">
        $0
      </text>

      {/* usage area + line */}
      <polygon points={area} className="pc-area" />
      <polyline points={line} className="pc-line" />

      {/* NOW marker */}
      <line x1={sx(elapsed)} y1={PAD.t} x2={sx(elapsed)} y2={sy(0)} className="pc-now" />
      <text x={sx(elapsed)} y={PAD.t - 2} className="pc-nowlabel" textAnchor="middle">
        now · {timePct}%
      </text>

      {/* x axis labels */}
      <text x={sx(0)} y={H - 6} className="pc-xlabel" textAnchor="start">
        start
      </text>
      <text x={sx(win)} y={H - 6} className="pc-xlabel" textAnchor="end">
        +{formatDuration(win)}
      </text>
      {usagePct != null && (
        <text x={sx(elapsed) + 6} y={sy(total) - 5} className="pc-usagelabel">
          {formatUSD(total)} · {usagePct}%
        </text>
      )}
    </svg>
  );
}
