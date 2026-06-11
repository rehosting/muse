import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SessionBacklink } from "../api/types";

/** A thin bar above the viewer showing which Investigations reference this
 * session (markup authored by the user's Claude Code over MCP, or by hand).
 * Each investigation links to its page; each anchored reference jumps the
 * current view to that step. Loads on mount — markup is persisted, not pushed. */
export default function SessionBacklinks({
  sessionId,
  onFocus,
}: {
  sessionId: string;
  onFocus: (uuid: string) => void;
}) {
  const [links, setLinks] = useState<SessionBacklink[]>([]);

  useEffect(() => {
    let ok = true;
    api
      .getSessionReferences(sessionId)
      .then((l) => ok && setLinks(l))
      .catch(() => ok && setLinks([]));
    return () => {
      ok = false;
    };
  }, [sessionId]);

  if (links.length === 0) return null;

  const byInv = new Map<
    string,
    { title: string; author: string; kind: string; refs: SessionBacklink[] }
  >();
  for (const l of links) {
    const g =
      byInv.get(l.investigation_id) ??
      { title: l.investigation_title, author: l.author, kind: l.kind, refs: [] };
    g.refs.push(l);
    byInv.set(l.investigation_id, g);
  }

  return (
    <div className="backlinks">
      <span className="backlinks-label">🔗 Referenced by</span>
      {[...byInv.entries()].map(([id, g]) => (
        <span key={id} className="backlink-chip">
          {g.kind === "retro" && <span className="retro-chip">retro</span>}
          <Link to={`/investigations/${id}`} className={`backlink-inv author-${g.author}`}>
            {g.title}
          </Link>
          {g.refs
            .filter((r) => r.ref.anchor_uuid)
            .map((r) => (
              <button
                key={r.ref.id}
                className="backlink-ref"
                title={r.ref.comment || r.ref.label || "jump to referenced step"}
                onClick={() => onFocus(r.ref.anchor_uuid as string)}
              >
                ↪ step
              </button>
            ))}
        </span>
      ))}
    </div>
  );
}
