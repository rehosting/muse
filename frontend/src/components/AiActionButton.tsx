import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AIJob } from "../api/types";

/** A button that enqueues one AI job (headless claude -p) and tracks it to
 * completion: label → "… 12s" → "✓" (or the error in the tooltip). Used for
 * AI summary / daily digest / weekly retro actions. */
export default function AiActionButton({
  label,
  title,
  enqueue,
  onDone,
  className = "action-btn",
}: {
  label: string;
  title?: string;
  enqueue: () => Promise<AIJob>;
  onDone?: (job: AIJob) => void;
  className?: string;
}) {
  const [job, setJob] = useState<AIJob | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const running = job != null && (job.status === "queued" || job.status === "running");

  useEffect(() => {
    if (!running || !job) return;
    const started = Date.now();
    const tick = async () => {
      setElapsed((Date.now() - started) / 1000);
      try {
        const j = await api.getAiJob(job.id);
        setJob(j);
        if (j.status === "done") onDone?.(j);
        else if (j.status === "error") setError(j.error ?? "failed");
        if (j.status === "queued" || j.status === "running") {
          timer.current = window.setTimeout(tick, 2000);
        }
      } catch {
        timer.current = window.setTimeout(tick, 4000);
      }
    };
    timer.current = window.setTimeout(tick, 1500);
    return () => window.clearTimeout(timer.current);
  }, [job?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    if (running) return;
    setError(null);
    setElapsed(0);
    try {
      setJob(await enqueue());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const text = error
    ? "⚠ AI failed"
    : running
      ? `… ${elapsed.toFixed(0)}s`
      : job?.status === "done"
        ? "✓ done"
        : label;
  return (
    <button
      className={className}
      title={error ?? title ?? label}
      disabled={running}
      onClick={start}
    >
      {text}
    </button>
  );
}
