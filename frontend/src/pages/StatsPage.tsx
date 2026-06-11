import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SessionSummary, StatsResponse, WindowStat } from "../api/types";
import { formatDuration, formatTokens, formatUSD, shortModel } from "../util/format";
import PaceChart from "../components/PaceChart";
import { usePolling } from "../hooks/usePolling";

export default function StatsPage() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [unhealthy, setUnhealthy] = useState<SessionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .listSessions()
      .then((ss) => {
        const cutoff = Date.now() - 7 * 86400_000;
        setUnhealthy(
          ss.filter(
            (s) =>
              s.health && s.health !== "ok" && new Date(s.mtime).getTime() >= cutoff,
          ),
        );
      })
      .catch(() => {});
    return api.getStats().then(setStats).catch((e) => setError(String(e)));
  }, []);
  usePolling(load, 12000);

  if (error)
    return (
      <div className="list-wrap">
        <div className="error-banner">{error}</div>
      </div>
    );
  if (!stats)
    return (
      <div className="list-wrap">
        <div className="empty">Computing stats…</div>
      </div>
    );

  const t = stats.totals;
  return (
    <div className="list-wrap">
      <h2 className="list-heading">Usage stats · across all sessions</h2>
      <p className="stat-note">
        Tokens are measured from your transcripts, deduped per API message, and
        cross-checked against Claude Code's own rollup below.{" "}
        <strong>Cost is the API list-price equivalent</strong> of those tokens — not your
        subscription bill. The authoritative limit % lives on Anthropic's server; the
        windows below estimate your pace against the plan budget.
      </p>

      {stats.plan && <PlanPanel plan={stats.plan} />}

      <div className="window-cards">
        <WindowTracker w={stats.hours} estimated={stats.plan?.budget_source !== "configured"} />
        <WindowTracker w={stats.week} estimated={stats.plan?.budget_source !== "configured"} />
      </div>

      {stats.insights && <InsightsPanel insights={stats.insights} />}

      {unhealthy.length > 0 && (
        <>
          <h3 className="stat-section">Sessions with issues · last 7 days</h3>
          <div className="journal-quiet" style={{ marginBottom: 14 }}>
            {unhealthy.map((s) => (
              <div key={s.session_id}>
                <span className={`health-chip health-${s.health}`}>
                  {s.health === "bad" ? "🔴" : "🟡"}
                </span>{" "}
                <Link to={`/sessions/${s.session_id}`}>{s.title}</Link>
                <span className="note-meta"> · {s.project_cwd ?? s.project_dir}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <h3 className="stat-section">Last 7 days · local time</h3>
      <DailyChart stats={stats} />

      <h3 className="stat-section">Activity by hour · local time</h3>
      <HourChart stats={stats} />

      <h3 className="stat-section">All-time totals</h3>
      <div className="stat-cards">
        <BigStat label="Cost · API-equiv" value={formatUSD(t.cost_usd)} />
        <BigStat label="Input tokens" value={formatTokens(t.input_tokens)} accent="in" />
        <BigStat label="Output tokens" value={formatTokens(t.output_tokens)} accent="out" />
        <BigStat
          label="Cache tokens"
          value={formatTokens(t.cache_creation_input_tokens + t.cache_read_input_tokens)}
        />
        <BigStat label="API messages" value={t.messages.toLocaleString()} />
        <BigStat label="Sessions" value={String(t.sessions)} />
      </div>

      {stats.claude_cache && <CrossCheck stats={stats} />}

      <h3 className="stat-section">Caching</h3>
      <div className="stat-cards">
        <BigStat label="Cache hit rate" value={`${(stats.cache_hit_rate * 100).toFixed(1)}%`} />
        <BigStat label="Est. cache savings" value={formatUSD(stats.cache_savings_usd)} accent="out" />
        <BigStat label="Cache read tokens" value={formatTokens(t.cache_read_input_tokens)} />
        <BigStat label="Cache write tokens" value={formatTokens(t.cache_creation_input_tokens)} />
      </div>
      <p className="stat-note">
        Hit rate = cache-read ÷ all prompt tokens. Caching is fully priced: cache writes ≈1.25×
        input, cache reads ≈0.1× input. Savings = what cache-reads would have cost at full input
        price.
      </p>

      <h3 className="stat-section">Where the cost goes</h3>
      <CostBreakdownBar stats={stats} />

      <h3 className="stat-section">Cost by model</h3>
      <ModelBars stats={stats} />

      <h3 className="stat-section">Top sessions by cost</h3>
      <table className="model-table">
        <tbody>
          {stats.top_sessions.map((s) => (
            <tr key={s.session_id}>
              <td className="model-name" style={{ textAlign: "left" }}>
                <Link to={`/sessions/${s.session_id}`}>{s.title}</Link>
              </td>
              <td>{s.messages} msgs</td>
              <td>{formatTokens(s.total_tokens)}</td>
              <td>{formatUSD(s.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="stat-section">Tool usage</h3>
      <div className="tool-usage">
        {stats.tools.map((t2) => (
          <span className="tool-chip" key={t2.name}>
            <span className="tool-chip-name">{t2.name}</span>
            <span className="tool-chip-count">{t2.count}</span>
          </span>
        ))}
      </div>

      <h3 className="stat-section">By project</h3>
      <table className="model-table">
        <tbody>
          {stats.by_project.map((p) => (
            <tr key={p.project}>
              <td className="model-name" style={{ textAlign: "left" }}>
                {p.project}
              </td>
              <td>{p.sessions} sess</td>
              <td>{p.messages} msgs</td>
              <td>{formatTokens(p.total_tokens)}</td>
              <td>{formatUSD(p.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="stat-section">By model</h3>
      <table className="model-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Msgs</th>
            <th>Input</th>
            <th>Output</th>
            <th>Cache</th>
            <th>Total</th>
            <th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {stats.by_model.map((m) => (
            <tr key={m.model}>
              <td className="model-name">{shortModel(m.model)}</td>
              <td>{m.messages.toLocaleString()}</td>
              <td>{formatTokens(m.input_tokens)}</td>
              <td>{formatTokens(m.output_tokens)}</td>
              <td>
                {formatTokens(m.cache_creation_input_tokens + m.cache_read_input_tokens)}
              </td>
              <td>{formatTokens(m.total_tokens)}</td>
              <td>{formatUSD(m.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="stat-foot">
        Updated {new Date(stats.generated_at).toLocaleTimeString()} · cost is an estimate from
        list prices
      </div>
    </div>
  );
}

function InsightsPanel({ insights }: { insights: NonNullable<StatsResponse["insights"]> }) {
  return (
    <div className="insights">
      <h3 className="stat-section">What's contributing to your limits usage?</h3>
      <p className="stat-note">
        Approximate, computed from local sessions over the last {insights.window_hours}h — these are
        independent characteristics of your usage, not a breakdown. (The exact session/week % used
        lives on Anthropic's server; muse can't read it.)
      </p>
      {insights.factors.map((f) => (
        <div className="insight" key={f.key}>
          <div className="insight-head">
            <span className="insight-pct">{f.pct}%</span> {f.label}
          </div>
          <div className="insight-advice">{f.advice}</div>
        </div>
      ))}
      {insights.by_subagent_type.length > 0 && (
        <div className="insight-subagents">
          <div className="insight-sub-head">Subagents · % of usage</div>
          {insights.by_subagent_type.map((s) => (
            <div className="insight-sub-row" key={s.agent_type}>
              <span className="insight-sub-name">{s.agent_type}</span>
              <span className="insight-sub-pct">{s.pct}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CrossCheck({ stats }: { stats: StatsResponse }) {
  const cc = stats.claude_cache!;
  const t = stats.totals;
  const pct = (muse: number, ref: number) => (ref ? ((muse - ref) / ref) * 100 : 0);
  const delta = (muse: number, ref: number) => {
    const d = pct(muse, ref);
    const cls = Math.abs(d) <= 5 ? "ok" : Math.abs(d) <= 20 ? "mid" : "high";
    return <span className={`xc-delta xc-${cls}`}>{d >= 0 ? "+" : ""}{d.toFixed(0)}%</span>;
  };
  const rows: Array<[string, string, string, number, number]> = [
    ["Cost", formatUSD(t.cost_usd), formatUSD(cc.cost_usd), t.cost_usd, cc.cost_usd],
    ["Tokens", formatTokens(t.total_tokens), formatTokens(cc.total_tokens), t.total_tokens, cc.total_tokens],
    ["Messages", t.messages.toLocaleString(), cc.total_messages.toLocaleString(), t.messages, cc.total_messages],
    ["Sessions", String(t.sessions), String(cc.total_sessions), t.sessions, cc.total_sessions],
  ];
  return (
    <div className="crosscheck">
      <h3 className="stat-section">
        Cross-check vs Claude Code{" "}
        <span className="xc-auth">authoritative · {cc.last_computed_date}</span>
      </h3>
      <table className="model-table xc-table">
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Metric</th>
            <th>muse (transcripts)</th>
            <th>Claude Code</th>
            <th>Δ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, museV, ccV, m, r]) => (
            <tr key={label}>
              <td className="model-name" style={{ textAlign: "left" }}>{label}</td>
              <td>{museV}</td>
              <td>{ccV}</td>
              <td>{delta(m, r)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="stat-note">
        Claude Code also reports {cc.total_tool_calls.toLocaleString()} tool calls. Small Δ is
        expected — muse counts subagent tokens and message-bearing lines slightly differently.
      </p>
    </div>
  );
}

function PlanPanel({ plan }: { plan: NonNullable<StatsResponse["plan"]> }) {
  const outOfCredits = plan.extra_usage_disabled_reason === "out_of_credits";
  return (
    <div className="plan-panel">
      <div className="plan-main">
        <span className="plan-label">{plan.label}</span>
        {plan.organization_name && <span className="plan-org">{plan.organization_name}</span>}
        {plan.has_extra_usage && (
          <span className={`plan-extra${outOfCredits ? " warn" : ""}`}>
            extra usage {outOfCredits ? "· out of credits" : "enabled"}
          </span>
        )}
      </div>
      <div className="plan-budgets">
        <span>
          5h budget <strong>{plan.five_hour_budget_usd ? formatUSD(plan.five_hour_budget_usd) : "—"}</strong>
        </span>
        <span>
          weekly budget{" "}
          <strong>{plan.weekly_budget_usd ? formatUSD(plan.weekly_budget_usd) : "—"}</strong>
        </span>
        <span className={`budget-source src-${plan.budget_source}`}>{plan.budget_source}</span>
      </div>
      {plan.budget_source === "estimated" && (
        <p className="stat-note">
          Budgets are rough estimates for <strong>{plan.label}</strong> — Anthropic doesn't publish
          exact 5-hour/weekly caps. Set <code>MUSE_LIMIT_5H_USD</code> / <code>MUSE_LIMIT_WEEK_USD</code>{" "}
          to your real limits. Note: muse's $ figure is cache-read heavy, so it overstates
          consumption toward plan limits.
        </p>
      )}
    </div>
  );
}

function BigStat({ label, value, accent }: { label: string; value: string; accent?: "in" | "out" }) {
  return (
    <div className="big-stat">
      <div className={`big-stat-value${accent ? ` accent-${accent}` : ""}`}>{value}</div>
      <div className="big-stat-label">{label}</div>
    </div>
  );
}

function WindowTracker({ w, estimated }: { w: WindowStat; estimated?: boolean }) {
  const timePct = Math.round((w.elapsed_seconds / w.window_seconds) * 100);
  const usagePct = w.budget_usd ? Math.round((w.cost_usd / w.budget_usd) * 100) : null;

  // Pace verdict: usage% vs time%.
  let verdict: { text: string; cls: string } | null = null;
  if (usagePct != null && w.messages > 0) {
    const diff = usagePct - timePct;
    if (diff > 10) verdict = { text: `ahead of pace (+${diff} pts)`, cls: "v-high" };
    else if (diff < -10) verdict = { text: `under pace (${diff} pts)`, cls: "v-low" };
    else verdict = { text: "on pace", cls: "v-mid" };
  }

  return (
    <div className="window-card">
      <div className="window-head">
        <span className="window-label">{w.label}</span>
        <span className="window-reset">
          {w.messages > 0 ? `resets in ${formatDuration(w.remaining_seconds)}` : "idle"}
        </span>
      </div>

      <PaceChart w={w} />

      <div className="window-compare">
        <span className="cmp time">⏱ {timePct}% through window</span>
        {usagePct != null ? (
          <span className="cmp usage">
            {estimated ? "≈" : ""}
            {usagePct}% of {estimated ? "est. " : ""}budget ($-equiv)
          </span>
        ) : (
          <span className="cmp usage dim">no budget set</span>
        )}
        {verdict && <span className={`cmp verdict ${verdict.cls}`}>{verdict.text}</span>}
      </div>

      <div className="window-usage">
        <span>
          <strong>{formatUSD(w.cost_usd)}</strong> cost
        </span>
        <span className="accent-in">
          <strong>{formatTokens(w.input_tokens)}</strong> in
        </span>
        <span className="accent-out">
          <strong>{formatTokens(w.output_tokens)}</strong> out
        </span>
        <span>
          <strong>{w.messages}</strong> msgs
        </span>
      </div>
    </div>
  );
}

function HourChart({ stats }: { stats: StatsResponse }) {
  const max = Math.max(0.01, ...stats.by_hour.map((h) => h.cost_usd));
  return (
    <div className="hour-chart">
      {stats.by_hour.map((h) => (
        <div className="hour-col" key={h.hour} title={`${h.hour}:00 — ${formatUSD(h.cost_usd)}, ${h.messages} msgs`}>
          <div className="hour-bar-wrap">
            <span className="hour-bar" style={{ height: `${(h.cost_usd / max) * 100}%` }} />
          </div>
          <div className="hour-label">{h.hour % 3 === 0 ? h.hour : ""}</div>
        </div>
      ))}
    </div>
  );
}

function CostBreakdownBar({ stats }: { stats: StatsResponse }) {
  const cb = stats.cost_breakdown;
  const segs = [
    { label: "output", value: cb.output, cls: "seg-output" },
    { label: "cache write", value: cb.cache_write, cls: "seg-cw" },
    { label: "cache read", value: cb.cache_read, cls: "seg-cr" },
    { label: "input", value: cb.input, cls: "seg-input" },
  ];
  const total = segs.reduce((a, s) => a + s.value, 0) || 1;
  return (
    <div className="cost-breakdown">
      <div className="cost-bar">
        {segs.map((s) => (
          <span
            key={s.label}
            className={`cost-seg ${s.cls}`}
            style={{ width: `${(s.value / total) * 100}%` }}
            title={`${s.label}: ${formatUSD(s.value)}`}
          />
        ))}
      </div>
      <div className="cost-legend">
        {segs.map((s) => (
          <span key={s.label} className="cost-legend-item">
            <span className={`cost-dot ${s.cls}`} />
            {s.label} <strong>{formatUSD(s.value)}</strong> ({Math.round((s.value / total) * 100)}%)
          </span>
        ))}
      </div>
    </div>
  );
}

function ModelBars({ stats }: { stats: StatsResponse }) {
  const max = Math.max(0.01, ...stats.by_model.map((m) => m.cost_usd));
  return (
    <div className="model-bars">
      {stats.by_model.map((m) => (
        <div className="model-bar-row" key={m.model}>
          <span className="model-bar-name">{shortModel(m.model)}</span>
          <span className="model-bar-track">
            <span className="model-bar-fill" style={{ width: `${(m.cost_usd / max) * 100}%` }} />
          </span>
          <span className="model-bar-val">{formatUSD(m.cost_usd)}</span>
        </div>
      ))}
    </div>
  );
}

function DailyChart({ stats }: { stats: StatsResponse }) {
  const max = Math.max(1, ...stats.daily.map((d) => d.cost_usd));
  return (
    <div className="daily-chart">
      {stats.daily.map((d) => (
        <div className="daily-col" key={d.date} title={`${d.date}: ${formatUSD(d.cost_usd)}`}>
          <div className="daily-bar-wrap">
            <span
              className="daily-bar"
              style={{ height: `${Math.max(2, (d.cost_usd / max) * 100)}%` }}
            />
          </div>
          <div className="daily-cost">{d.cost_usd > 0 ? formatUSD(d.cost_usd) : "—"}</div>
          <div className="daily-date">{d.date.slice(5)}</div>
        </div>
      ))}
    </div>
  );
}
