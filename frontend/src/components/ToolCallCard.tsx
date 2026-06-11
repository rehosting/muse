import type { ToolUse } from "../api/types";
import { summarize } from "./renderers";

function status(tool: ToolUse): { label: string; cls: string } {
  if (!tool.result) return { label: "pending", cls: "status-pending" };
  if (tool.result.is_error) return { label: "error", cls: "status-error" };
  if (tool.result.truncated) return { label: "truncated", cls: "status-truncated" };
  return { label: "ok", cls: "status-ok" };
}

export default function ToolCallCard({
  tool,
  onClick,
}: {
  tool: ToolUse;
  onClick: () => void;
}) {
  const st = status(tool);
  return (
    <div className="tool-card" onClick={onClick}>
      <div className="tool-card-head">
        <span className="tool-name">{tool.name}</span>
        <span className="tool-summary">{summarize(tool.name, tool.input)}</span>
        {tool.subagent && (
          <span className="subagent-pill">{tool.subagent.agent_type}</span>
        )}
        <span className={`tool-status ${st.cls}`}>{st.label}</span>
      </div>
    </div>
  );
}
