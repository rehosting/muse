import type { ToolRenderer } from "./types";
import BashRenderer from "./BashRenderer";
import ReadRenderer from "./ReadRenderer";
import EditRenderer from "./EditRenderer";
import WriteRenderer from "./WriteRenderer";
import AgentRenderer from "./AgentRenderer";
import TodoRenderer from "./TodoRenderer";
import SearchRenderer from "./SearchRenderer";
import WebRenderer from "./WebRenderer";
import DefaultRenderer from "./DefaultRenderer";

// Tool name -> renderer. Anything not listed falls back to DefaultRenderer.
const REGISTRY: Record<string, ToolRenderer> = {
  Bash: BashRenderer,
  BashOutput: BashRenderer,
  Read: ReadRenderer,
  Edit: EditRenderer,
  MultiEdit: EditRenderer,
  Write: WriteRenderer,
  Agent: AgentRenderer,
  Task: AgentRenderer,
  TodoWrite: TodoRenderer,
  Grep: SearchRenderer,
  Glob: SearchRenderer,
  WebFetch: WebRenderer,
  WebSearch: WebRenderer,
};

export function rendererFor(toolName: string): ToolRenderer {
  return REGISTRY[toolName] ?? DefaultRenderer;
}

/** One-line summary of a tool call for the compact card. */
export function summarize(name: string, input: Record<string, unknown>): string {
  switch (name) {
    case "Bash":
      return String(input.command ?? "");
    case "Read":
    case "Edit":
    case "MultiEdit":
    case "Write":
      return String(input.file_path ?? "");
    case "Agent":
    case "Task":
      return String(input.description ?? input.subagent_type ?? "");
    case "Grep":
      return String(input.pattern ?? "");
    case "Glob":
      return String(input.pattern ?? "");
    case "TodoWrite":
      return Array.isArray(input.todos) ? `${input.todos.length} todos` : "";
    case "WebFetch":
      return String(input.url ?? "");
    case "WebSearch":
      return String(input.query ?? "");
    default: {
      const firstVal = Object.values(input)[0];
      return firstVal !== undefined ? String(firstVal).slice(0, 120) : "";
    }
  }
}
