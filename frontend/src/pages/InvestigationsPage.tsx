import { useCallback, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { InvestigationSummary } from "../api/types";
import { relativeTime } from "../util/format";
import { usePolling } from "../hooks/usePolling";
import AiActionButton from "../components/AiActionButton";

type Tab = "all" | "investigation" | "retro";

const TABS: { key: Tab; label: string }[] = [
  { key: "all", label: "All" },
  { key: "investigation", label: "Investigations" },
  { key: "retro", label: "Retros" },
];

/** List of Investigations — markup documents the user's Claude Code authors over
 * MCP (or the user writes by hand) that reference real sessions. Retros (session
 * post-mortems) share the same shape and live under their own tab. */
export default function InvestigationsPage() {
  const [items, setItems] = useState<InvestigationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("all");
  const navigate = useNavigate();

  const load = useCallback(
    () =>
      api
        .listInvestigations()
        .then(setItems)
        .catch((e) => setError(String(e))),
    [],
  );
  usePolling(load, 8000);

  const create = async () => {
    const inv = await api.createInvestigation({ title: "Untitled investigation" });
    navigate(`/investigations/${inv.id}`);
  };

  if (error)
    return (
      <div className="list-wrap">
        <div className="error-banner">{error}</div>
      </div>
    );

  const visible = items?.filter((i) => tab === "all" || (i.kind ?? "investigation") === tab);
  const retroCount = items?.filter((i) => i.kind === "retro").length ?? 0;

  return (
    <div className="list-wrap">
      <div className="inv-list-head">
        <h2 className="list-heading">Investigations · most recent first</h2>
        <div className="layout-switch" role="group" aria-label="Filter by kind">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`layout-btn inv-tab${tab === t.key ? " active" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              {t.key === "retro" && retroCount > 0 ? ` (${retroCount})` : ""}
            </button>
          ))}
        </div>
        <AiActionButton
          label="✦ Draft weekly retro"
          title="AI retrospective of the current week's sessions (headless claude; lands under Retros)"
          enqueue={() => api.generateWeeklyRetro()}
          onDone={(job) => {
            const ref = job.result?.output_ref;
            if (ref?.type === "investigation") navigate(`/investigations/${ref.id}`);
            else load();
          }}
        />
        <button className="action-btn primary" onClick={create}>
          + New
        </button>
      </div>
      <p className="inv-intro">
        Findings authored by your Claude Code over MCP — or by you — that reference
        evidence inside sessions. Connect with{" "}
        <code>claude mcp add --transport http muse http://127.0.0.1:8848/mcp</code>.
      </p>
      {visible && visible.length === 0 && (
        <div className="empty">
          {tab === "retro" ? "No retros yet — ask Claude to create_retrospective a session." : "No investigations yet."}
        </div>
      )}
      {!items && <div className="empty">Loading…</div>}
      {visible?.map((i) => (
        <Link to={`/investigations/${i.id}`} className="session-card" key={i.id}>
          <div className="session-card-top">
            <span className={`author-badge author-${i.author}`}>
              {i.author === "ai" ? "AI" : "you"}
            </span>
            {i.kind === "retro" && <span className="chip retro-chip">retro</span>}
            <div className="title">{i.title}</div>
            {i.status && i.status !== "open" && (
              <span className="chip">{i.status}</span>
            )}
          </div>
          <div className="session-meta">
            <span>{i.ref_count} references</span>
            {i.updated_at && <span>{relativeTime(i.updated_at)}</span>}
          </div>
        </Link>
      ))}
    </div>
  );
}
