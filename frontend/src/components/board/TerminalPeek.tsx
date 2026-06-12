import { useCallback, useState } from "react";
import { api } from "../../api/client";
import { usePolling } from "../../hooks/usePolling";

/** Raw tmux capture-pane view — shows what the transcript can't (permission
 * prompts, the input line, rate-limit banners). Polls only while open. */
export default function TerminalPeek({ sessionId }: { sessionId: string }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.getTerminal(sessionId, 30);
      if (r.ok) {
        setText(r.text);
        setErr(null);
      } else {
        setErr(r.error ?? "capture failed");
      }
    } catch (e) {
      setErr(String(e));
    }
  }, [sessionId]);
  usePolling(load, 2000);

  if (err) return <div className="term-peek term-peek-err">⚠ {err}</div>;
  if (text === null) return <div className="term-peek dim">capturing…</div>;
  return <pre className="term-peek">{text.replace(/\s+$/, "")}</pre>;
}
