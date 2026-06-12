import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { BoardCard } from "../api/types";
import SessionCard from "../components/board/SessionCard";
import { useBoardStream } from "../hooks/useBoardStream";

/** Mission control: every recent session as a live card, triaged into
 * Needs attention → Working → Stopped. One SSE connection feeds all cards
 * (status, context %, activity line, health) — reply to a waiting session
 * without leaving the page. */

type Group = "attention" | "working" | "stopped";

export function groupOf(c: BoardCard): Group {
  if (c.state === "stopped") return "stopped";
  if (
    c.state === "waiting" ||
    c.live_status === "waiting" ||
    c.live_status === "idle" ||
    c.waiting_for ||
    c.health === "bad" ||
    c.last_activity?.kind === "error"
  ) {
    return "attention";
  }
  return "working";
}

export default function BoardPage() {
  const { cards, live } = useBoardStream();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const navigate = useNavigate();

  const groups = useMemo(() => {
    const g: Record<Group, BoardCard[]> = { attention: [], working: [], stopped: [] };
    for (const c of cards ?? []) g[groupOf(c)].push(c);
    return g;
  }, [cards]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const liveIds = (cards ?? [])
    .filter((c) => c.state === "live")
    .map((c) => c.session_id);

  if (!cards) {
    return (
      <div className="list-wrap">
        <div className="empty">Loading…</div>
      </div>
    );
  }

  return (
    <div className="list-wrap board-wrap">
      <div className="board-head">
        <h2 className="list-heading">
          Mission control
          <span className={`board-conn ${live ? "board-conn-live" : ""}`}
            title={live ? "live (SSE)" : "polling fallback"}>
            {live ? "● live" : "○ polling"}
          </span>
        </h2>
        <div className="board-actions">
          <button
            className="action-btn follow-live-btn"
            disabled={!liveIds.length}
            onClick={() => navigate(`/follow?sessions=${liveIds.join(",")}`)}
            title="Open a live-tailing pane for every live session"
          >
            ● Follow all live ({liveIds.length})
          </button>
          <button
            className="action-btn follow-btn"
            disabled={!selected.size}
            onClick={() => navigate(`/follow?sessions=${[...selected].join(",")}`)}
          >
            Follow selected ({selected.size}) →
          </button>
        </div>
      </div>

      {groups.attention.length > 0 && (
        <section className="board-group">
          <h3 className="board-group-title attention">
            ✋ Needs attention ({groups.attention.length})
          </h3>
          {groups.attention.map((c) => (
            <SessionCard key={c.session_id} card={c}
              selected={selected.has(c.session_id)} onToggle={toggle} />
          ))}
        </section>
      )}

      {groups.working.length > 0 && (
        <section className="board-group">
          <h3 className="board-group-title working">
            ● Working ({groups.working.length})
          </h3>
          {groups.working.map((c) => (
            <SessionCard key={c.session_id} card={c}
              selected={selected.has(c.session_id)} onToggle={toggle} />
          ))}
        </section>
      )}

      {groups.attention.length === 0 && groups.working.length === 0 && (
        <div className="empty">No active sessions — everything below is history.</div>
      )}

      <details className="board-stopped">
        <summary className="board-group-title stopped">
          ⏹ Stopped recently ({groups.stopped.length})
        </summary>
        {groups.stopped.map((c) => (
          <SessionCard key={c.session_id} card={c}
            selected={selected.has(c.session_id)} onToggle={toggle} />
        ))}
      </details>
    </div>
  );
}
