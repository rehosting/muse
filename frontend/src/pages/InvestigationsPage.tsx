import { useCallback, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { InvestigationSummary } from "../api/types";
import { relativeTime } from "../util/format";
import { usePolling } from "../hooks/usePolling";

/** List of Investigations — markup documents the user's Claude Code authors over
 * MCP (or the user writes by hand) that reference real sessions. */
export default function InvestigationsPage() {
  const [items, setItems] = useState<InvestigationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  return (
    <div className="list-wrap">
      <div className="inv-list-head">
        <h2 className="list-heading">Investigations · most recent first</h2>
        <button className="action-btn primary" onClick={create}>
          + New
        </button>
      </div>
      <p className="inv-intro">
        Findings authored by your Claude Code over MCP — or by you — that reference
        evidence inside sessions. Connect with{" "}
        <code>claude mcp add --transport http muse http://127.0.0.1:8848/mcp</code>.
      </p>
      {items && items.length === 0 && (
        <div className="empty">No investigations yet.</div>
      )}
      {!items && <div className="empty">Loading…</div>}
      {items?.map((i) => (
        <Link to={`/investigations/${i.id}`} className="session-card" key={i.id}>
          <div className="session-card-top">
            <span className={`author-badge author-${i.author}`}>
              {i.author === "ai" ? "AI" : "you"}
            </span>
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
