import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import FollowPane from "../components/FollowPane";

/** Multi-session follow: live-tailing panes for every session in ?sessions=a,b,c */
export default function FollowPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const ids = useMemo(() => {
    const raw = searchParams.get("sessions");
    return raw ? raw.split(",").filter(Boolean) : [];
  }, [searchParams]);

  const remove = (id: string) => {
    const next = ids.filter((x) => x !== id);
    const params = new URLSearchParams(searchParams);
    if (next.length) params.set("sessions", next.join(","));
    else params.delete("sessions");
    setSearchParams(params);
  };

  if (ids.length === 0) {
    return (
      <div className="list-wrap">
        <div className="empty">
          No sessions selected. Pick sessions on the <Link to="/board">Monitor</Link> and click
          “Follow selected”.
        </div>
      </div>
    );
  }

  return (
    <div className="follow-page">
      <div className="follow-bar">
        <span className="follow-count">Following {ids.length} session{ids.length > 1 ? "s" : ""}</span>
        <Link className="action-btn" to="/board">
          + add from monitor
        </Link>
      </div>
      <div
        className="follow-grid"
        style={{ gridTemplateColumns: `repeat(${Math.min(ids.length, 3)}, 1fr)` }}
      >
        {ids.map((id) => (
          <FollowPane key={id} sessionId={id} onRemove={remove} />
        ))}
      </div>
    </div>
  );
}
