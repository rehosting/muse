import { useState } from "react";
import { api } from "../../api/client";
import type { ToolResult } from "../../api/types";
import Collapsible from "../Collapsible";
import CodeBlock from "../CodeBlock";
import Markdown from "../Markdown";

export type ResultHint = "auto" | "bash" | "markdown" | "plain";

function looksLikeMarkdown(t: string): boolean {
  return (
    /(^|\n)#{1,6}\s/.test(t) ||
    t.includes("```") ||
    /(^|\n)\s*\|.+\|\s*(\n|$)/.test(t) ||
    /\[[^\]]+\]\([^)]+\)/.test(t) ||
    /\*\*[^*\n]+\*\*/.test(t)
  );
}

/** Render result text using the best format for its content. */
function ResultBody({ text, hint }: { text: string; hint: ResultHint }) {
  if (!text) return <pre className="code">(empty)</pre>;

  const mode =
    hint === "markdown"
      ? "markdown"
      : hint === "plain"
        ? "plain"
        : hint === "bash"
          ? looksLikeMarkdown(text)
            ? "markdown"
            : "bash"
          : looksLikeMarkdown(text)
            ? "markdown"
            : "plain";

  if (mode === "markdown") return <Markdown>{text}</Markdown>;
  if (mode === "bash") return <CodeBlock code={text} lang="bash" />;
  return <pre className="code">{text}</pre>;
}

/** Renders a tool result, lazily loading persisted (truncated) output on demand. */
export default function ResultView({
  result,
  sessionId,
  hint = "auto",
}: {
  result: ToolResult | null;
  sessionId: string;
  hint?: ResultHint;
}) {
  const [full, setFull] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (!result) {
    return (
      <>
        <div className="section-label">Result</div>
        <pre className="code status-pending">pending…</pre>
      </>
    );
  }

  const loadFull = async () => {
    if (!result.cache_id) return;
    setLoading(true);
    try {
      const out = await api.getToolResult(sessionId, result.cache_id);
      setFull(out.content);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="section-label">
        Result {result.is_error && <span className="status-error">· error</span>}
        {result.truncated && <span className="status-truncated">· truncated</span>}
      </div>

      {result.truncated ? (
        full !== null ? (
          <Collapsible text={full}>
            <ResultBody text={full} hint={hint} />
          </Collapsible>
        ) : (
          <>
            <ResultBody text={result.preview ?? "(no preview)"} hint={hint} />
            <button className="load-more" onClick={loadFull} disabled={loading}>
              {loading ? "Loading…" : "Load full output"}
            </button>
          </>
        )
      ) : (
        <Collapsible text={result.content ?? ""}>
          <ResultBody text={result.content ?? ""} hint={hint} />
        </Collapsible>
      )}
    </>
  );
}
