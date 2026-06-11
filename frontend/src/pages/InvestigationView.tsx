import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Investigation, InvestigationRef } from "../api/types";
import InvestigationBody from "../components/InvestigationBody";
import ResizableSplit from "../components/ResizableSplit";
import SessionPane from "../components/SessionPane";
import { relativeTime } from "../util/format";

type Active = { sessionId: string; anchor: string | null } | null;

/** One Investigation, as a split view: the markdown body + its references on the
 * left, and a live session conversation pane on the right. Clicking a reference
 * (inline citation or chip) loads that session in the right pane, scrolled to the
 * cited step — so you read the finding and its evidence side by side. */
export default function InvestigationView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [inv, setInv] = useState<Investigation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [active, setActive] = useState<Active>(null);

  const openRef = useCallback(
    (sessionId: string, anchor: string | null) => setActive({ sessionId, anchor }),
    [],
  );

  const load = useCallback(() => {
    if (!id) return;
    api
      .getInvestigation(id)
      .then((i) => {
        setInv(i);
        setDraftTitle(i.title);
        setDraftBody(i.body);
        // Default the right pane to the first reference so it isn't empty.
        setActive((cur) =>
          cur ??
          (i.refs[0]
            ? { sessionId: i.refs[0].session_id, anchor: i.refs[0].anchor_uuid }
            : null),
        );
      })
      .catch((e) => setError(String(e)));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!id) return;
    const updated = await api.updateInvestigation(id, {
      title: draftTitle,
      body: draftBody,
    });
    setInv(updated);
    setEditing(false);
  };

  const remove = async () => {
    if (!id || !window.confirm("Delete this investigation?")) return;
    await api.deleteInvestigation(id);
    navigate("/investigations");
  };

  const removeRef = async (refId: string) => {
    if (!id) return;
    await api.removeReference(id, refId);
    load();
  };

  if (error)
    return (
      <div className="inv-wrap">
        <div className="error-banner">{error}</div>
      </div>
    );
  if (!inv) return <div className="inv-wrap"><div className="empty">Loading…</div></div>;

  const isActive = (r: InvestigationRef) =>
    active?.sessionId === r.session_id && (active?.anchor ?? null) === (r.anchor_uuid ?? null);

  const left = (
    <div className="inv-left">
      <div className="inv-title-row">
        <span className={`author-badge author-${inv.author}`}>
          {inv.author === "ai" ? "AI" : "you"}
        </span>
        {editing ? (
          <input
            className="inv-title-input"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
          />
        ) : (
          <h2 className="inv-title">{inv.title}</h2>
        )}
        {inv.status && inv.status !== "open" && <span className="chip">{inv.status}</span>}
        {inv.updated_at && (
          <span className="inv-time">updated {relativeTime(inv.updated_at)}</span>
        )}
      </div>

      {editing ? (
        <textarea
          className="inv-body-input"
          value={draftBody}
          onChange={(e) => setDraftBody(e.target.value)}
          rows={16}
          placeholder="Markdown body…"
        />
      ) : (
        <div className="inv-body">
          {inv.body ? (
            <InvestigationBody body={inv.body} refs={inv.refs} onOpenRef={openRef} />
          ) : (
            <em className="text-dim">No body.</em>
          )}
        </div>
      )}

      <h3 className="inv-refs-head">References ({inv.refs.length})</h3>
      {inv.refs.length === 0 && <div className="text-dim">No references.</div>}
      <ul className="inv-refs">
        {inv.refs.map((r) => (
          <li key={r.id} className="inv-ref">
            <button
              className={`inv-ref-link${isActive(r) ? " active" : ""}`}
              onClick={() => openRef(r.session_id, r.anchor_uuid)}
              title={r.comment || undefined}
            >
              {r.label || r.session_id}
              {r.anchor_uuid && <span className="inv-ref-anchor"> @ step</span>}
            </button>
            {r.comment && <span className="inv-ref-comment">{r.comment}</span>}
            <button
              className="inv-ref-del"
              title="Remove reference"
              onClick={() => removeRef(r.id)}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <div className="inv-wrap inv-wrap-split">
      <div className="inv-head">
        <Link to="/investigations" className="inv-back">
          ← Investigations
        </Link>
        <div className="inv-head-actions">
          {editing ? (
            <>
              <button className="action-btn primary" onClick={save}>
                Save
              </button>
              <button className="action-btn" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button className="action-btn" onClick={() => setEditing(true)}>
                Edit
              </button>
              <button className="action-btn" onClick={remove}>
                Delete
              </button>
            </>
          )}
        </div>
      </div>

      <ResizableSplit direction="row" storageKey="inv-split">
        {left}
        <SessionPane sessionId={active?.sessionId ?? null} focusAnchor={active?.anchor ?? null} />
      </ResizableSplit>
    </div>
  );
}
