import type { ThreadItem, ToolUse } from "../api/types";
import MessageBubble from "./MessageBubble";

export default function MessageThread({
  items,
  onOpenTool,
}: {
  items: ThreadItem[];
  onOpenTool: (tool: ToolUse) => void;
}) {
  return (
    <div className="thread">
      {items.map((item) => (
        <MessageBubble key={item.uuid} item={item} onOpenTool={onOpenTool} />
      ))}
    </div>
  );
}
