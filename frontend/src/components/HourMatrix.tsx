import type { MatrixCell } from "../api/types";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CELL = 15;
const GAP = 2;
const LEFT = 34;
const TOP = 16;

type Mode = "activity" | "errors" | "commits";
const RAMP: Record<Mode, string> = { activity: "act", errors: "err", commits: "com" };

/** 24×7 hour-of-day × weekday matrix. The selected mode drives the color ramp;
 * every cell's tooltip shows all three counts so you can cross-read (e.g.
 * "errors cluster at 11pm, commits land at 10am") in any mode. */
export default function HourMatrix({
  cells,
  mode,
}: {
  cells: MatrixCell[];
  mode: Mode;
}) {
  const get = (c: MatrixCell) =>
    mode === "activity" ? c.activity : mode === "errors" ? c.errors : c.commits;
  const max = Math.max(1, ...cells.map(get));
  const grid = new Map(cells.map((c) => [`${c.dow}-${c.hour}`, c]));
  const width = LEFT + 24 * (CELL + GAP) + 4;
  const height = TOP + 7 * (CELL + GAP) + 4;

  const intensity = (v: number) => (v <= 0 ? 0 : 1 + Math.min(4, Math.ceil((v / max) * 4)));

  return (
    <svg className="hour-matrix" viewBox={`0 0 ${width} ${height}`} width="100%">
      {[0, 6, 12, 18, 23].map((h) => (
        <text key={h} x={LEFT + h * (CELL + GAP)} y={11} className="hm-axis">{h}</text>
      ))}
      {DOW.map((d, row) => (
        <text key={d} x={0} y={TOP + row * (CELL + GAP) + CELL - 3} className="hm-axis">{d}</text>
      ))}
      {DOW.map((_, row) =>
        Array.from({ length: 24 }, (_, h) => {
          const c = grid.get(`${row}-${h}`);
          const v = c ? get(c) : 0;
          return (
            <rect
              key={`${row}-${h}`}
              x={LEFT + h * (CELL + GAP)}
              y={TOP + row * (CELL + GAP)}
              width={CELL}
              height={CELL}
              rx={2}
              className={`hm-cell hm-${RAMP[mode]}${intensity(v)}`}
            >
              <title>
                {DOW[row]} {h}:00 — {c?.activity ?? 0} msgs · {c?.errors ?? 0} errors · {c?.commits ?? 0} commits
              </title>
            </rect>
          );
        }),
      )}
    </svg>
  );
}
