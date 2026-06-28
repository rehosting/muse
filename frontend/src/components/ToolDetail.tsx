import { useEffect, useState } from "react";
import type { ToolUse } from "../api/types";
import { api } from "../api/client";
import { rendererFor } from "./renderers";
import CodeBlock from "./CodeBlock";
import Collapsible from "./Collapsible";

/** The detail content for a tool call. Used both embedded (panel) and in the modal. */
export default function ToolDetail({
  tool,
  sessionId,
  onOpenSubagent,
}: {
  tool: ToolUse;
  sessionId: string;
  onOpenSubagent: (agentId: string) => void;
}) {
  const Renderer = rendererFor(tool.name);

  // The thread ships large tool results truncated (preview only). Pull the full
  // persisted output so the raw JSON below is genuinely complete for every tool.
  const cacheId = tool.result?.truncated ? tool.result.cache_id : null;
  const [fullResult, setFullResult] = useState<string | null>(null);
  useEffect(() => {
    setFullResult(null);
    if (!cacheId) return;
    let alive = true;
    api
      .getToolResult(sessionId, cacheId)
      .then((out) => alive && setFullResult(out.content))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [cacheId, sessionId]);

  const toolForJson =
    fullResult != null && tool.result
      ? { ...tool, result: { ...tool.result, content: fullResult, truncated: false } }
      : tool;
  const raw = JSON.stringify(toolForJson, null, 2);
  const pendingFull = cacheId != null && fullResult == null;

  return (
    <div>
      <div className="detail-head-inline">
        <span className="tool-name">{tool.name}</span>
        <code style={{ color: "var(--text-dim)", fontSize: 11 }}>{tool.id}</code>
      </div>

      <Renderer tool={tool} sessionId={sessionId} onOpenSubagent={onOpenSubagent} />

      <div className="detail-raw">
        <div className="section-label">
          Raw tool data
          {pendingFull && <span className="cc-dim"> · loading full output…</span>}
        </div>
        <Collapsible text={raw} collapsedLabel="Show full raw tool data">
          <CodeBlock code={raw} lang="json" />
        </Collapsible>
      </div>
    </div>
  );
}
