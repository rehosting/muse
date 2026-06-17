import type { HeatDay } from "../api/types";
import { formatUSD } from "../util/format";

const CELL = 13;
const GAP = 3;
const DOW_LABELS = ["", "M", "", "W", "", "F", ""];

/** GitHub-style cost heatmap. Intensity uses cost QUANTILES (not max), so one
 * outlier day doesn't flatten the rest. Click a day → its journal. */
export default function CalendarHeatmap({
  days,
  onSelectDay,
}: {
  days: HeatDay[];
  onSelectDay: (day: string) => void;
}) {
  if (days.length === 0) {
    return <div className="empty">No usage history yet.</div>;
  }
  const byDay = new Map(days.map((d) => [d.day, d]));
  const last = new Date(`${days[days.length - 1].day}T12:00:00`);
  const first = new Date(`${days[0].day}T12:00:00`);
  // Start on the Monday on/before the first day.
  const start = new Date(first);
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));

  // Quantile thresholds from non-zero costs.
  const costs = days.map((d) => d.cost_usd).filter((c) => c > 0).sort((a, b) => a - b);
  const q = (p: number) => costs[Math.min(costs.length - 1, Math.floor(p * costs.length))] ?? 0;
  const thresholds = [q(0.2), q(0.4), q(0.6), q(0.8)];
  const level = (c: number) =>
    c <= 0 ? 0 : 1 + thresholds.filter((t) => c >= t).length;

  const cells: { x: number; y: number; day: string; d?: HeatDay }[] = [];
  const cur = new Date(start);
  let col = 0;
  const monthTicks: { x: number; label: string }[] = [];
  let lastMonth = -1;
  while (cur <= last) {
    const dow = (cur.getDay() + 6) % 7; // 0=Mon
    if (dow === 0) col++;
    const iso = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}-${String(cur.getDate()).padStart(2, "0")}`;
    cells.push({ x: col * (CELL + GAP), y: dow * (CELL + GAP), day: iso, d: byDay.get(iso) });
    if (cur.getMonth() !== lastMonth && dow === 0) {
      monthTicks.push({ x: col * (CELL + GAP), label: cur.toLocaleString(undefined, { month: "short" }) });
      lastMonth = cur.getMonth();
    }
    cur.setDate(cur.getDate() + 1);
  }
  const width = (col + 2) * (CELL + GAP) + 24;
  const height = 7 * (CELL + GAP) + 22;

  return (
    <svg className="cal-heatmap" viewBox={`0 0 ${width} ${height}`}
      width={width} height={height} style={{ maxWidth: "100%" }}>
      {monthTicks.map((m, i) => (
        <text key={i} x={m.x + 24} y={8} className="cal-month">{m.label}</text>
      ))}
      {DOW_LABELS.map((l, i) => (
        l ? <text key={i} x={0} y={14 + i * (CELL + GAP) + CELL - 3} className="cal-dow">{l}</text> : null
      ))}
      <g transform="translate(24, 14)">
        {cells.map((c) => (
          <rect
            key={c.day}
            x={c.x}
            y={c.y}
            width={CELL}
            height={CELL}
            rx={2}
            className={`cal-cell cal-l${c.d ? level(c.d.cost_usd) : 0}`}
            onClick={() => onSelectDay(c.day)}
          >
            <title>
              {c.day}: {c.d ? formatUSD(c.d.cost_usd) : "$0"}
              {c.d && c.d.work_tokens ? ` · ${Math.round(c.d.work_tokens / 1000)}k tok` : ""}
            </title>
          </rect>
        ))}
      </g>
    </svg>
  );
}
