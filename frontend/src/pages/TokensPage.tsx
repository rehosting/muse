import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { TokenUsage } from "../api/types";

const WINDOWS = [1, 7, 30, 90] as const;

const fmtUsd = (n: number) =>
  n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${n.toFixed(2)}`;

const fmtTokens = (n: number) => {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
};

const pct = (n: number | undefined) => (n == null ? "—" : `${Math.round(n * 100)}%`);

/**
 * Token usage, as measured by the tokentracker CLI rather than by muse.
 *
 * muse derives its own numbers elsewhere (Stats/Insights); this page deliberately
 * passes the tool's accounting straight through — edit turns, first-pass rate and
 * subagent attribution are things it already does better than we do.
 *
 * Two speeds, surfaced honestly: the default read comes from the CLI's own cache and
 * is fast enough to load on navigation, while "Re-parse" re-reads every transcript and
 * takes ~10s, so it's a button the user presses, never something that happens on a poll.
 */
export default function TokensPage() {
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<TokenUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reparsing, setReparsing] = useState(false);

  const load = useCallback(async (window: number, refresh = false) => {
    refresh ? setReparsing(true) : setLoading(true);
    setError(null);
    try {
      setData(await api.getTokenUsage(window, refresh));
    } catch (e) {
      // The tool being absent/slow is an environment condition, not a crash — say which.
      setError(e instanceof Error ? e.message : "could not reach the token tracker");
    } finally {
      setLoading(false);
      setReparsing(false);
    }
  }, []);

  useEffect(() => {
    load(days);
  }, [days, load]);

  const s = data?.summary;

  return (
    <div className="list-wrap tokens-page">
      <div className="stats-head">
        <h2 className="list-heading">Tokens</h2>
        <div className="tokens-controls">
          {WINDOWS.map((w) => (
            <button
              key={w}
              className={`chip${w === days ? " active" : ""}`}
              onClick={() => setDays(w)}
            >
              {w}d
            </button>
          ))}
          <button
            className="chip"
            disabled={reparsing}
            title="Re-read every transcript instead of the tracker's cache (~10s)"
            onClick={() => load(days, true)}
          >
            {reparsing ? "re-parsing…" : "↻ Re-parse"}
          </button>
        </div>
      </div>

      {error && (
        <div className="tokens-error">
          {error}
          <div className="tokens-error-hint">
            muse shells out to <code>tokentracker-cli</code>. Install it with{" "}
            <code>npm i -g tokentracker-cli</code>, or point muse at it with{" "}
            <code>MUSE_TOKENTRACKER_CMD</code>.
          </div>
        </div>
      )}

      {loading && !data && <div className="empty">loading usage…</div>}

      {data && s && (
        <>
          <div className="stat-cards">
            <BigStat label={`spend · last ${data.days}d`} value={fmtUsd(s.cost_usd ?? 0)} />
            <BigStat label="tokens" value={fmtTokens(s.total_tokens ?? 0)} />
            <BigStat label="sessions" value={String(data.session_count)} />
            <BigStat label="cost / edit" value={fmtUsd(s.cost_per_edit ?? 0)} />
          </div>

          <div className="stat-cards">
            <BigStat label="edit turns" value={String(s.edit_turns ?? 0)} />
            <BigStat label="first-pass rate" value={pct(s.first_pass_rate)} />
            <BigStat label="productive sessions" value={pct(s.productive_rate)} />
            <BigStat label="retries" value={String(s.retries ?? 0)} />
          </div>

          <h3 className="list-heading">By model</h3>
          <table className="tokens-table">
            <thead>
              <tr>
                <th>model</th>
                <th className="num">sessions</th>
                <th className="num">tokens</th>
                <th className="num">cost</th>
                <th className="num">edit turns</th>
                <th className="num">first pass</th>
              </tr>
            </thead>
            <tbody>
              {data.by_model.map((m) => (
                <tr key={m.model}>
                  <td className="mono">{m.model}</td>
                  <td className="num">{m.sessions}</td>
                  <td className="num">{fmtTokens(m.total_tokens ?? 0)}</td>
                  <td className="num">{fmtUsd(m.cost_usd ?? 0)}</td>
                  <td className="num">{m.edit_turns ?? 0}</td>
                  <td className="num">{pct(m.first_pass_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {data.subagents.length > 0 && (
            <>
              <h3 className="list-heading">Subagents</h3>
              <table className="tokens-table">
                <thead>
                  <tr>
                    <th>agent</th>
                    <th className="num">calls</th>
                    <th className="num">sessions</th>
                    <th className="num">tokens</th>
                    <th className="num">cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.subagents.map((a) => (
                    <tr key={a.name}>
                      <td className="mono">{a.name}</td>
                      <td className="num">{a.calls}</td>
                      <td className="num">{a.sessions}</td>
                      <td className="num">{fmtTokens(a.total_tokens ?? 0)}</td>
                      <td className="num">{fmtUsd(a.cost_usd ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <h3 className="list-heading">Sessions ({data.session_count})</h3>
          <table className="tokens-table">
            <thead>
              <tr>
                <th>project</th>
                <th>model</th>
                <th>src</th>
                <th className="num">turns</th>
                <th className="num">edits</th>
                <th className="num">tokens</th>
                <th className="num">cost</th>
                <th>started</th>
              </tr>
            </thead>
            <tbody>
              {[...data.sessions]
                .sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0))
                .map((x) => (
                  <tr key={x.session_hash}>
                    <td className="mono">{x.project_key || "—"}</td>
                    <td className="mono dim">{x.model || "—"}</td>
                    <td className="dim">{x.source}</td>
                    <td className="num">{x.turns ?? 0}</td>
                    <td className="num">{x.edit_turns ?? 0}</td>
                    <td className="num">{fmtTokens(x.total_tokens ?? 0)}</td>
                    <td className="num">{fmtUsd(x.cost_usd ?? 0)}</td>
                    <td className="dim">
                      {x.started_at ? new Date(x.started_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>

          <div className="tokens-foot">
            {data.provenance.privacy === "metadata-only" && "metadata only · "}
            source: tokentracker-cli ({data.refreshed ? "re-parsed" : "tracker cache"}) ·
            read {new Date(data.generated_at).toLocaleTimeString()}
          </div>
        </>
      )}
    </div>
  );
}

function BigStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="big-stat">
      <div className="big-stat-value">{value}</div>
      <div className="big-stat-label">{label}</div>
    </div>
  );
}
