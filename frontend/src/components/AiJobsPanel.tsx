import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { AIJob } from "../api/types";
import { usePolling } from "../hooks/usePolling";

const KIND_LABELS: Record<string, string> = {
  ask: "ask",
  session_summary: "summary",
  daily_digest: "daily digest",
  weekly_retro: "weekly retro",
};

function age(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** Compact list of recent AI jobs (status dot, kind, age, duration, cost) with
 * cancel for queued/running ones. Polls while anything is in flight. */
export default function AiJobsPanel({
  onOpen,
}: {
  onOpen?: (job: AIJob) => void;
}) {
  const [jobs, setJobs] = useState<AIJob[]>([]);

  const refresh = useCallback(async () => {
    setJobs(await api.listAiJobs(15));
  }, []);
  const active = jobs.some((j) => j.status === "queued" || j.status === "running");
  usePolling(refresh, active ? 2000 : 15000);

  if (jobs.length === 0) return null;
  return (
    <div className="ai-jobs">
      <div className="ai-jobs-title">Recent jobs</div>
      {jobs.map((j) => (
        <div
          key={j.id}
          className={`ai-job-row${onOpen && j.result?.answer_md ? " clickable" : ""}`}
          onClick={() => j.result?.answer_md && onOpen?.(j)}
        >
          <span className={`ai-dot ai-${j.status}`} title={j.status} />
          <span className="ai-job-kind">{KIND_LABELS[j.kind] ?? j.kind}</span>
          <span className="ai-job-meta">
            {j.status === "error"
              ? (j.error ?? "error").slice(0, 60)
              : (j.params.question ?? j.params.session_id ?? j.params.day ?? j.params.week_start ?? "").slice(0, 60)}
          </span>
          <span className="ai-job-right">
            {j.duration_ms != null && <span>{(j.duration_ms / 1000).toFixed(1)}s</span>}
            {j.cost_usd != null && <span>${j.cost_usd.toFixed(3)}</span>}
            <span className="dim">{age(j.created_at)}</span>
            {(j.status === "queued" || j.status === "running") && (
              <button
                className="ai-cancel"
                title="Cancel this job"
                onClick={(e) => {
                  e.stopPropagation();
                  api.cancelAiJob(j.id).then(refresh);
                }}
              >
                ✕
              </button>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
