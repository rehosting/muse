import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AIJob, AIStatus } from "../api/types";
import AiJobsPanel from "../components/AiJobsPanel";
import InvestigationBody from "../components/InvestigationBody";

/** Ask muse: one-shot Q&A over the whole session corpus. The backend packs
 * FTS-matched session digests and runs a single headless `claude -p`; we
 * enqueue, then poll the job until it lands. Answers cite sessions with
 * /sessions/<id>?focus= deep links (InvestigationBody renders those in-app). */
export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [job, setJob] = useState<AIJob | null>(null);
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const pollRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    api.getAiStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // Poll the active job every 2s until it leaves queued/running.
  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    const started = Date.now();
    const tick = async () => {
      setElapsed((Date.now() - started) / 1000);
      try {
        const j = await api.getAiJob(job.id);
        setJob(j);
        if (j.status === "queued" || j.status === "running") {
          pollRef.current = window.setTimeout(tick, 2000);
        }
      } catch {
        pollRef.current = window.setTimeout(tick, 4000);
      }
    };
    pollRef.current = window.setTimeout(tick, 1500);
    return () => window.clearTimeout(pollRef.current);
  }, [job?.id, job?.status === "queued" || job?.status === "running"]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = useCallback(async () => {
    const q = question.trim();
    if (!q) return;
    setElapsed(0);
    setJob(await api.askMuse(q));
  }, [question]);

  const busy = job?.status === "queued" || job?.status === "running";

  return (
    <div className="list-wrap ask-wrap">
      <div className="journal-head">
        <h2 className="list-heading">✻ Ask muse</h2>
        <span className="dim ask-sub">
          a question across your whole session history — answered by{" "}
          {status?.model ?? "…"} over the most relevant transcripts
        </span>
      </div>

      {status && !status.available && (
        <div className="ask-warning">
          ⚠ <code>claude</code> CLI not found — the AI layer is unavailable.
        </div>
      )}
      {status?.last_error && (
        <div className="ask-warning dim" title={status.last_error}>
          last job error: {status.last_error.slice(0, 120)}
        </div>
      )}

      <div className="ask-input-row">
        <textarea
          className="ask-input"
          rows={3}
          placeholder='e.g. "How did I fix the database-is-locked errors?" or "What approaches have I tried for parsing codex transcripts?"'
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          disabled={busy}
        />
        <button
          className="action-btn ask-submit"
          onClick={submit}
          disabled={busy || !question.trim() || status?.available === false}
          title="Ask (⌘↵)"
        >
          {busy ? `… ${elapsed.toFixed(0)}s` : "Ask"}
        </button>
      </div>

      {job && (
        <div className="ask-answer">
          {busy && (
            <div className="ask-progress">
              <span className={`ai-dot ai-${job.status}`} />
              {job.status === "queued" ? "queued…" : "thinking…"}{" "}
              <span className="dim">
                {elapsed.toFixed(0)}s — a cross-session answer typically takes 30–120s
              </span>
              <button
                className="ai-cancel"
                onClick={() => api.cancelAiJob(job.id).then(() => api.getAiJob(job.id).then(setJob))}
              >
                cancel
              </button>
            </div>
          )}
          {job.status === "error" && (
            <div className="ask-warning">⚠ {job.error}</div>
          )}
          {job.status === "cancelled" && <div className="dim">cancelled</div>}
          {job.status === "done" && job.result?.answer_md && (
            <>
              <InvestigationBody body={job.result.answer_md} refs={[]} />
              <div className="ask-answer-meta dim">
                {job.model}
                {job.duration_ms != null && ` · ${(job.duration_ms / 1000).toFixed(1)}s`}
                {job.cost_usd != null && ` · $${job.cost_usd.toFixed(4)}`}
              </div>
            </>
          )}
        </div>
      )}

      <AiJobsPanel onOpen={(j) => setJob(j)} />
    </div>
  );
}
