import { useEffect } from "react";
import type { ToolUse } from "../api/types";
import ToolDetail from "./ToolDetail";

/** Modal slide-over wrapper around ToolDetail — used in layout mode 1. */
export default function ToolDetailPanel({
  tool,
  sessionId,
  onClose,
  onOpenSubagent,
}: {
  tool: ToolUse;
  sessionId: string;
  onClose: () => void;
  onOpenSubagent: (agentId: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="detail-overlay" onClick={onClose} />
      <aside className="detail-panel">
        <div className="detail-head">
          <span className="tool-name">{tool.name}</span>
          <button className="close-btn" onClick={onClose}>
            Close ✕
          </button>
        </div>
        <div className="detail-body">
          <ToolDetail tool={tool} sessionId={sessionId} onOpenSubagent={onOpenSubagent} />
        </div>
      </aside>
    </>
  );
}
