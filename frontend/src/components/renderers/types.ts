import type { ToolUse } from "../../api/types";

export interface RendererProps {
  tool: ToolUse;
  sessionId: string;
  /** Open a subagent transcript by agent id (drill-down). */
  onOpenSubagent: (agentId: string) => void;
}

export type ToolRenderer = (props: RendererProps) => JSX.Element;
