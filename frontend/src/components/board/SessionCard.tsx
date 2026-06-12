import { useState } from "react";
import { Link } from "react-router-dom";
import type { BoardCard } from "../../api/types";
import { formatTokens, relativeTime, shortModel } from "../../util/format";
import HealthBadges from "./HealthBadges";
import ReplyBox from "./ReplyBox";
import TerminalPeek from "./TerminalPeek";

const ACTIVITY_ICON: Record<string, string> = {
  assistant_text: "⏺",
  tool_call: "⏺",
  user: ">",
  error: "⚠",
};

function statusDot(card: BoardCard): { cls: string; label: string } {
  if (card.live_status === "busy") return { cls: "dot-busy", label: "busy — Claude's turn" };
  if (card.live_status === "waiting" || card.waiting_for)
    return { cls: "dot-waiting", label: `waiting for ${card.waiting_for ?? "you"}` };
  if (card.live_status === "idle") return { cls: "dot-idle", label: "idle — awaiting your reply" };
  if (card.state === "live") return { cls: "dot-busy", label: "active" };
  if (card.state === "waiting") return { cls: "dot-waiting", label: "waiting for you" };
  return { cls: "dot-stopped", label: "stopped" };
}

function idleLabel(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

/** One session on the mission-control board: status, context meter, live
 * activity line, health badges, reply box, terminal peek. */
export default function SessionCard({
  card,
  selected,
  onToggle,
}: {
  card: BoardCard;
  selected: boolean;
  onToggle: (id: string) => void;
}) {
  const [peek, setPeek] = useState(false);
  const dot = statusDot(card);
  const act = card.last_activity;
  const interactive = card.provider === "claude" && card.state !== "stopped";

  return (
    <div className={`scard scard-${card.state}${card.health === "bad" ? " scard-bad" : ""}`}>
      <div className="scard-top">
        <input
          type="checkbox"
          className="scard-check"
          title="Select for multi-follow"
          checked={selected}
          onChange={() => onToggle(card.session_id)}
        />
        <span className={`scard-dot ${dot.cls}`} title={dot.label} />
        <Link to={`/sessions/${card.session_id}`} className="scard-title">
          {card.title}
        </Link>
        <span className="scard-right dim">
          {card.model && <span>{shortModel(card.model)}</span>}
          {card.total_tokens > 0 && (
            <span title="work tokens · cost">
              {formatTokens(card.total_tokens)}
              {card.cost_usd > 0 && ` · $${card.cost_usd.toFixed(2)}`}
            </span>
          )}
          <span title={new Date(card.mtime).toLocaleString()}>
            {card.state === "stopped"
              ? relativeTime(card.mtime)
              : idleLabel((Date.now() - new Date(card.mtime).getTime()) / 1000)}
          </span>
        </span>
      </div>

      <div className="scard-meta dim">
        {card.project_cwd && <span className="scard-proj">{card.project_cwd}</span>}
        {card.git_branch && <span className="scard-branch">⎇ {card.git_branch}</span>}
        {card.context_pct != null && (
          <span
            className={`scard-ctx${card.context_pct >= 85 ? " ctx-hot" : ""}`}
            title="Context window used"
          >
            ctx {Math.round(card.context_pct)}%
          </span>
        )}
        <HealthBadges health={card.health} flags={card.health_flags} />
      </div>

      {act && act.text && (
        <div className={`scard-activity${act.kind === "error" ? " scard-act-err" : ""}`}>
          <span className="scard-act-icon">{ACTIVITY_ICON[act.kind] ?? "·"}</span>
          {act.kind === "tool_call" && act.tool && (
            <span className="scard-act-tool">{act.tool}</span>
          )}
          <span className="scard-act-text">{act.text}</span>
        </div>
      )}

      {interactive && (
        <>
          <div className="scard-actions">
            <ReplyBox
              sessionId={card.session_id}
              hasPane={card.has_pane}
              busy={card.live_status === "busy"}
            />
            <button
              className={`action-btn scard-peek-btn${peek ? " active" : ""}`}
              disabled={!card.has_pane}
              title={card.has_pane ? "Peek at the raw terminal" : "No tmux pane matched"}
              onClick={() => setPeek((p) => !p)}
            >
              ▤
            </button>
            <Link
              className="action-btn"
              to={`/follow?sessions=${card.session_id}`}
              title="Live-tail this session"
            >
              ⊕ follow
            </Link>
          </div>
          {peek && card.has_pane && <TerminalPeek sessionId={card.session_id} />}
        </>
      )}
    </div>
  );
}
