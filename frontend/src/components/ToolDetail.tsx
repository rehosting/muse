import type { ToolUse } from "../api/types";
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
  const raw = JSON.stringify(tool, null, 2);
  return (
    <div>
      <div className="detail-head-inline">
        <span className="tool-name">{tool.name}</span>
        <code style={{ color: "var(--text-dim)", fontSize: 11 }}>{tool.id}</code>
      </div>

      <Renderer tool={tool} sessionId={sessionId} onOpenSubagent={onOpenSubagent} />

      <div className="detail-raw">
        <div className="section-label">Raw tool data</div>
        <Collapsible text={raw} collapsedLabel="Show full raw tool data">
          <CodeBlock code={raw} lang="json" />
        </Collapsible>
      </div>
    </div>
  );
}
