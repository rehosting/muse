import { useState } from "react";
import type { ThreadItem, ToolUse } from "../api/types";
import { shortModel } from "../util/format";
import ToolCallCard from "./ToolCallCard";
import UsageStats from "./UsageStats";

export default function MessageBubble({
  item,
  onOpenTool,
}: {
  item: ThreadItem;
  onOpenTool: (tool: ToolUse) => void;
}) {
  const roleClass =
    item.role === "user"
      ? "role-user"
      : item.role === "assistant"
        ? "role-assistant"
        : "role-system";

  return (
    <div className="message">
      <div className="message-head">
        <span className={`role-label ${roleClass}`}>{item.role}</span>
        {item.model && (
          <span style={{ color: "var(--text-dim)", fontSize: 11 }}>
            {shortModel(item.model)}
          </span>
        )}
        {item.usage && <UsageStats usage={item.usage} />}
        {item.timestamp && (
          <span className="ts">{new Date(item.timestamp).toLocaleTimeString()}</span>
        )}
      </div>
      <div className="message-body">
        {item.text && <p>{item.text}</p>}
        {item.blocks.map((b, i) => {
          if (b.kind === "text" && b.text) return <p key={i}>{b.text}</p>;
          if (b.kind === "thinking" && b.text) return <Thinking key={i} text={b.text} />;
          if (b.kind === "tool_use" && b.tool_use)
            return (
              <ToolCallCard
                key={i}
                tool={b.tool_use}
                onClick={() => onOpenTool(b.tool_use!)}
              />
            );
          return null;
        })}
      </div>
    </div>
  );
}

function Thinking({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="collapsible-toggle" onClick={() => setOpen(!open)}>
        {open ? "▾ thinking" : "▸ thinking"}
      </button>
      {open && <div className="thinking">{text}</div>}
    </div>
  );
}
