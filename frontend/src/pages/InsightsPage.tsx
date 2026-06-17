import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { OutcomesResponse, SessionOutcome, TimelineResponse } from "../api/types";
import CalendarHeatmap from "../components/CalendarHeatmap";
import HourMatrix from "../components/HourMatrix";
import ProjectTimeline from "../components/ProjectTimeline";
import { usePolling } from "../hooks/usePolling";
import { formatUSD } from "../util/format";

const RANGES = [
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
  { days: 0, label: "All" },
];

const MATRIX_MODES = [
  { key: "activity", label: "Activity" },
  { key: "errors", label: "Errors" },
  { key: "commits", label: "Commits" },
] as const;

function shipped(o: SessionOutcome): number {
  return o.commits_high + o.commits_medium;
}

function OutcomeRow({ o }: { o: SessionOutcome }) {
  return (
    <tr>
      <td>
        <Link to={`/sessions/${o.session_id}`} className="ins-sess">{o.title}</Link>
        {o.commit_subjects[0] && <div className="ins-subj dim">{o.commit_subjects[0]}</div>}
      </td>
      <td className="num">{formatUSD(o.cost_usd)}</td>
      <td className="num">
        {shipped(o) > 0 ? <span className="ins-ship">{shipped(o)}</span> : <span className="dim">0</span>}
        {o.commits_low > 0 && <span className="dim"> (+{o.commits_low}?)</span>}
      </td>
      <td>{o.health && o.health !== "ok" ? <span className={`health-badge health-${o.health}`}>{o.health}</span> : <span className="dim">ok</span>}</td>
    </tr>
  );
}

export default function InsightsPage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<OutcomesResponse | null>(null);
  const [matrixMode, setMatrixMode] = useState<(typeof MATRIX_MODES)[number]["key"]>("commits");
  const [project, setProject] = useState<string>("");
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const navigate = useNavigate();

  const load = useCallback(
    () => api.getInsights(days).then(setData).catch(() => setData(null)),
    [days],
  );
  useEffect(() => { load(); }, [load]);
  usePolling(load, 30000);

  // Default the timeline project to the costliest one once data lands.
  const projects = useMemo(
    () => data?.by_project.map((p) => p.key).filter((k) => k !== "(unknown)") ?? [],
    [data],
  );
  useEffect(() => {
    if (!project && data && data.by_project.length) {
      const costliest = [...data.by_project].sort((a, b) => b.cost_usd - a.cost_usd)[0];
      if (costliest) setProject(costliest.key);
    }
  }, [data, project]);
  useEffect(() => {
    if (!project) return;
    const d = days === 0 ? 90 : days;
    api.getInsightsTimeline(project, d).then(setTimeline).catch(() => setTimeline(null));
  }, [project, days]);

  if (!data) return <div className="list-wrap"><div className="empty">Loading…</div></div>;

  return (
    <div className="list-wrap insights-wrap">
      <div className="journal-head">
        <h2 className="list-heading">Insights — what your sessions produced</h2>
        <div className="layout-switch" role="group">
          {RANGES.map((r) => (
            <button key={r.days}
              className={`layout-btn inv-tab${days === r.days ? " active" : ""}`}
              onClick={() => setDays(r.days)}>{r.label}</button>
          ))}
        </div>
        <Link to="/stats" className="action-btn">Spend stats →</Link>
      </div>
      <p className="inv-intro">
        Commits are linked to sessions by evidence (timing + file overlap + branch),
        not authorship proof — only high+medium confidence count as “shipped”; low
        is shown separately. <Link to="/files">Look up a commit hash →</Link>
      </p>

      <section className="ins-section">
        <h3 className="ins-h">Daily cost</h3>
        <CalendarHeatmap days={data.calendar} onSelectDay={(d) => navigate(`/journal?day=${d}`)} />
      </section>

      <section className="ins-section">
        <div className="ins-h-row">
          <h3 className="ins-h">When you work</h3>
          <div className="layout-switch" role="group">
            {MATRIX_MODES.map((m) => (
              <button key={m.key}
                className={`layout-btn inv-tab${matrixMode === m.key ? " active" : ""}`}
                onClick={() => setMatrixMode(m.key)}>{m.label}</button>
            ))}
          </div>
        </div>
        <HourMatrix cells={data.matrix} mode={matrixMode} />
      </section>

      <section className="ins-section ins-cols">
        <div>
          <h3 className="ins-h">Most productive <span className="dim">(commits / $)</span></h3>
          <table className="model-table">
            <thead><tr><th>Session</th><th>Cost</th><th>Shipped</th><th>Health</th></tr></thead>
            <tbody>
              {data.most_productive.length
                ? data.most_productive.map((o) => <OutcomeRow key={o.session_id} o={o} />)
                : <tr><td colSpan={4} className="dim">No shipped commits in range.</td></tr>}
            </tbody>
          </table>
        </div>
        <div>
          <h3 className="ins-h">Most wasteful <span className="dim">(cost, no commits)</span></h3>
          <table className="model-table">
            <thead><tr><th>Session</th><th>Cost</th><th>Shipped</th><th>Health</th></tr></thead>
            <tbody>
              {data.most_wasteful.length
                ? data.most_wasteful.map((o) => <OutcomeRow key={o.session_id} o={o} />)
                : <tr><td colSpan={4} className="dim">Nothing flagged.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="ins-section ins-cols">
        <div>
          <h3 className="ins-h">By model</h3>
          <RatioTable rows={data.by_model} />
        </div>
        <div>
          <h3 className="ins-h">By project</h3>
          <RatioTable rows={data.by_project} />
        </div>
      </section>

      <section className="ins-section">
        <div className="ins-h-row">
          <h3 className="ins-h">Project timeline</h3>
          <select className="ap-select" value={project} onChange={(e) => setProject(e.target.value)}>
            {projects.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        {timeline ? <ProjectTimeline data={timeline} /> : <div className="dim">Pick a project.</div>}
        <div className="tl-legend dim">
          ● high · ◯ medium · gray = low/unmatched · bar color = session health
        </div>
      </section>
    </div>
  );
}

function RatioTable({ rows }: { rows: OutcomesResponse["by_model"] }) {
  const max = Math.max(1, ...rows.map((r) => r.commits_per_10usd ?? 0));
  return (
    <table className="model-table">
      <thead><tr><th>Key</th><th>$</th><th>Shipped</th><th>/$10</th></tr></thead>
      <tbody>
        {rows.slice(0, 8).map((r) => (
          <tr key={r.key}>
            <td className="ins-key">{r.key}</td>
            <td className="num">{formatUSD(r.cost_usd)}</td>
            <td className="num">{r.commits}{r.commits_low > 0 && <span className="dim"> (+{r.commits_low}?)</span>}</td>
            <td className="num">
              {r.commits_per_10usd == null ? <span className="dim">—</span> : (
                <span className="ratio-bar-wrap">
                  <span className="ratio-bar" style={{ width: `${(r.commits_per_10usd / max) * 100}%` }} />
                  {r.commits_per_10usd}
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
