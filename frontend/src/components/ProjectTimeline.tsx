import { useNavigate } from "react-router-dom";
import type { TimelineResponse } from "../api/types";
import { formatUSD } from "../util/format";

const ROW_H = 16;
const LANE_CAP = 12;
const LEFT = 8;
const RIGHT = 8;
const TOP = 18;
const W = 720;

const HEALTH_CLASS: Record<string, string> = { ok: "tl-ok", warn: "tl-warn", bad: "tl-bad" };

/** Sessions packed into greedy non-overlapping lanes over a time axis, with
 * commits as ticks on a bottom lane (solid=high, hollow=medium, gray=low/
 * unmatched). The range buttons are the zoom; no drag-pan (deliberate cut). */
export default function ProjectTimeline({ data }: { data: TimelineResponse }) {
  const navigate = useNavigate();
  const start = data.start ? new Date(data.start).getTime() : 0;
  const end = data.end ? new Date(data.end).getTime() : start + 1;
  const span = Math.max(1, end - start);
  const innerW = W - LEFT - RIGHT;
  const x = (ts: string | null) =>
    LEFT + (((ts ? new Date(ts).getTime() : start) - start) / span) * innerW;

  // Greedy lane packing by start time.
  const sorted = [...data.sessions]
    .filter((s) => s.started || s.ended)
    .sort((a, b) => (a.started ?? a.ended ?? "").localeCompare(b.started ?? b.ended ?? ""));
  const laneEnds: number[] = [];
  const placed: { lane: number; s: (typeof sorted)[number] }[] = [];
  let overflow = 0;
  for (const s of sorted) {
    const x0 = x(s.started ?? s.ended);
    let lane = laneEnds.findIndex((e) => e <= x0);
    if (lane === -1) {
      if (laneEnds.length >= LANE_CAP) {
        overflow++;
        continue;
      }
      lane = laneEnds.length;
      laneEnds.push(0);
    }
    laneEnds[lane] = Math.max(x(s.ended ?? s.started) + 4, x0 + 4);
    placed.push({ lane, s });
  }
  const lanes = laneEnds.length || 1;
  const commitsY = TOP + lanes * ROW_H + 10;
  const height = commitsY + 24;

  // Day gridlines.
  const ticks: { x: number; label: string }[] = [];
  const day = 86400000;
  for (let t = Math.ceil(start / day) * day; t <= end; t += day) {
    if ((t - start) / span > 0.02 && (end - t) / span > 0.02) {
      ticks.push({ x: LEFT + ((t - start) / span) * innerW, label: new Date(t).toLocaleDateString(undefined, { month: "numeric", day: "numeric" }) });
    }
  }

  const allCommits = [
    ...data.sessions.flatMap((s) => s.commits),
    ...data.unmatched_commits,
  ];

  return (
    <svg className="proj-timeline" viewBox={`0 0 ${W} ${height}`} width="100%">
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={t.x} y1={TOP} x2={t.x} y2={commitsY} className="tl-grid" />
          <text x={t.x + 2} y={12} className="tl-axis">{t.label}</text>
        </g>
      ))}
      {placed.map(({ lane, s }) => {
        const x0 = x(s.started ?? s.ended);
        const x1 = Math.max(x0 + 4, x(s.ended ?? s.started));
        return (
          <rect
            key={s.session_id}
            x={x0}
            y={TOP + lane * ROW_H}
            width={x1 - x0}
            height={ROW_H - 4}
            rx={3}
            className={`tl-bar ${HEALTH_CLASS[s.health ?? ""] ?? "tl-none"}`}
            onClick={() => navigate(`/sessions/${s.session_id}`)}
          >
            <title>
              {s.title} — {formatUSD(s.cost_usd)} · {s.commits.length} commit(s)
            </title>
          </rect>
        );
      })}
      <line x1={LEFT} y1={commitsY} x2={W - RIGHT} y2={commitsY} className="tl-grid" />
      {allCommits.map((c, i) => (
        <circle
          key={`${c.commit_hash}-${i}`}
          cx={x(c.ts)}
          cy={commitsY}
          r={3.5}
          className={`tl-commit tl-c-${c.confidence ?? "none"}`}
        >
          <title>{c.commit_hash.slice(0, 8)} — {c.subject} ({c.confidence ?? "unmatched"})</title>
        </circle>
      ))}
      {overflow > 0 && (
        <text x={LEFT} y={height - 4} className="tl-axis">+{overflow} more sessions not shown</text>
      )}
    </svg>
  );
}
