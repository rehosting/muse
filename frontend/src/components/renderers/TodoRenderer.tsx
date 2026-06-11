import type { RendererProps } from "./types";
import ResultView from "./ResultView";

const ICON: Record<string, string> = {
  completed: "✓",
  in_progress: "◐",
  pending: "○",
};

/** TodoWrite: render the task checklist with status icons. */
export default function TodoRenderer({ tool, sessionId }: RendererProps) {
  const todos = Array.isArray(tool.input.todos)
    ? (tool.input.todos as Array<Record<string, unknown>>)
    : [];
  return (
    <div>
      <div className="section-label">Todos ({todos.length})</div>
      <ul className="todo-list">
        {todos.map((t, i) => {
          const status = String(t.status ?? "pending");
          const text = String(
            status === "in_progress" && t.activeForm ? t.activeForm : t.content ?? "",
          );
          return (
            <li key={i} className={`todo todo-${status}`}>
              <span className="todo-icon">{ICON[status] ?? "○"}</span>
              <span className="todo-text">{text}</span>
            </li>
          );
        })}
      </ul>
      <ResultView result={tool.result} sessionId={sessionId} hint="plain" />
    </div>
  );
}
