import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { SessionSummary } from "../api/types";
import ResizableSplit from "../components/ResizableSplit";
import SessionPane from "../components/SessionPane";
import { formatTokens, relativeTime } from "../util/format";

/** Side-by-side comparison of two sessions (e.g. the same task attempted twice,
 * or before/after a CLAUDE.md change). Reuses the investigation split-view's
 * SessionPane — no transcript alignment, just two synced-height panes with a
 * thin stats header each. */
export default function ComparePage() {
  const [params, setParams] = useSearchParams();
  const a = params.get("a");
  const b = params.get("b");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);

  useEffect(() => {
    api.listSessions().then(setSessions).catch(() => {});
  }, []);

  const setSide = (side: "a" | "b", sid: string) => {
    const next = new URLSearchParams(params);
    if (sid) next.set(side, sid);
    else next.delete(side);
    setParams(next, { replace: true });
  };

  const side = (key: "a" | "b", sid: string | null) => {
    const summ = sessions.find((s) => s.session_id === sid);
    return (
      <div className="compare-side">
        <div className="compare-head">
          <select
            className="compare-pick"
            value={sid ?? ""}
            onChange={(e) => setSide(key, e.target.value)}
          >
            <option value="">— pick a session —</option>
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                {s.title}
              </option>
            ))}
          </select>
          {summ && (
            <span className="compare-stats">
              {summ.message_count} msgs
              {summ.total_tokens > 0 && ` · ${formatTokens(summ.total_tokens)} tok`}
              {summ.health && summ.health !== "ok" && (
                <span className={`health-chip health-${summ.health}`}>
                  {" "}
                  {summ.health === "bad" ? "🔴" : "🟡"}
                </span>
              )}
              {` · ${relativeTime(summ.mtime)}`}
            </span>
          )}
        </div>
        <SessionPane sessionId={sid} focusAnchor={null} />
      </div>
    );
  };

  return (
    <div className="compare-wrap">
      <ResizableSplit direction="row" storageKey="muse.split.compare">
        {side("a", a)}
        {side("b", b)}
      </ResizableSplit>
    </div>
  );
}
