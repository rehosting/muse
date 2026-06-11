import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SessionHealth } from "../api/types";

/** Failure-pattern bar for the session viewer: retry loops, error spirals, and
 * permission-denial clusters, each focus-linking to the offending step. Hidden
 * entirely for healthy sessions. */
export default function HealthBar({
  sessionId,
  onFocus,
}: {
  sessionId: string;
  onFocus: (uuid: string) => void;
}) {
  const [health, setHealth] = useState<SessionHealth | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let ok = true;
    setHealth(null);
    api
      .getSessionHealth(sessionId)
      .then((h) => ok && setHealth(h))
      .catch(() => ok && setHealth(null));
    return () => {
      ok = false;
    };
  }, [sessionId]);

  if (!health || health.score === "ok") return null;

  const patternCount =
    health.retry_loops.length +
    health.error_spirals.length +
    (health.permission_denials.length > 0 ? 1 : 0);

  return (
    <div className={`notes-bar health-bar health-${health.score}`}>
      <button className="notes-toggle" onClick={() => setOpen((o) => !o)}>
        {health.score === "bad" ? "🔴" : "🟡"} Health: {health.score} — {patternCount}{" "}
        pattern{patternCount === 1 ? "" : "s"}, {health.error_count} error
        {health.error_count === 1 ? "" : "s"} {open ? "▾" : "▸"}
      </button>
      {open && (
        <ul className="notes-list">
          {health.retry_loops.map((l, i) => (
            <li key={`loop-${i}`} className="note-row">
              <span className="note-kind">🔁</span>
              <span className="note-body">
                Retry loop: <code>{l.tool}</code> × {l.times} — {l.label}
              </span>
              {l.anchors[0] && (
                <button className="backlink-ref" onClick={() => onFocus(l.anchors[0]!)}>
                  ↪ step
                </button>
              )}
            </li>
          ))}
          {health.error_spirals.map((s, i) => (
            <li key={`spiral-${i}`} className="note-row">
              <span className="note-kind">🌀</span>
              <span className="note-body">
                Error spiral: {s.errors}/{s.window} tool results failed
              </span>
              {s.start_anchor && (
                <button className="backlink-ref" onClick={() => onFocus(s.start_anchor!)}>
                  ↪ step
                </button>
              )}
            </li>
          ))}
          {health.permission_denials.length > 0 && (
            <li className="note-row">
              <span className="note-kind">⛔</span>
              <span className="note-body">
                {health.permission_denials.length} permission denials
              </span>
              {health.permission_denials[0].anchor && (
                <button
                  className="backlink-ref"
                  onClick={() => onFocus(health.permission_denials[0].anchor!)}
                >
                  ↪ step
                </button>
              )}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
