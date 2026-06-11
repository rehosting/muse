import type { RendererProps } from "./types";
import ResultView from "./ResultView";

export default function AgentRenderer({ tool, sessionId, onOpenSubagent }: RendererProps) {
  const sub = tool.subagent;
  const prompt = tool.input.prompt ? String(tool.input.prompt) : "";
  const subagentType =
    sub?.agent_type ?? (tool.input.subagent_type ? String(tool.input.subagent_type) : "agent");
  const desc = sub?.description ?? (tool.input.description ? String(tool.input.description) : "");

  return (
    <div>
      <div className="section-label">Subagent</div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8 }}>
        <span className="subagent-pill">{subagentType}</span>
        <span style={{ color: "var(--text-dim)" }}>{desc}</span>
      </div>
      {sub ? (
        <button className="load-more" onClick={() => onOpenSubagent(sub.agent_id)}>
          Open subagent transcript →
        </button>
      ) : (
        <div style={{ color: "var(--text-dim)", fontSize: 12 }}>
          (no transcript on disk for this agent)
        </div>
      )}

      <div className="section-label">Prompt</div>
      <pre className="code">{prompt}</pre>

      <ResultView result={tool.result} sessionId={sessionId} hint="markdown" />
    </div>
  );
}
