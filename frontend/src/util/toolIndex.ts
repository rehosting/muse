import type { ThreadItem, ToolUse } from "../api/types";

export interface ToolEntry {
  tool: ToolUse;
  messageUuid: string;
  timestamp: string | null;
  index: number;
}

/** Flatten every tool_use in a thread, in conversation order. */
export function flattenTools(items: ThreadItem[]): ToolEntry[] {
  const out: ToolEntry[] = [];
  for (const item of items) {
    for (const block of item.blocks) {
      if (block.kind === "tool_use" && block.tool_use) {
        out.push({
          tool: block.tool_use,
          messageUuid: item.uuid,
          timestamp: item.timestamp,
          index: out.length + 1,
        });
      }
    }
  }
  return out;
}

export function toolMap(items: ThreadItem[]): Map<string, ToolUse> {
  const m = new Map<string, ToolUse>();
  for (const item of items) {
    for (const block of item.blocks) {
      if (block.tool_use) m.set(block.tool_use.id, block.tool_use);
    }
  }
  return m;
}

export function toolStatus(tool: ToolUse): "ok" | "error" | "truncated" | "pending" {
  if (!tool.result) return "pending";
  if (tool.result.is_error) return "error";
  if (tool.result.truncated) return "truncated";
  return "ok";
}

/**
 * A lower-level, chronological event stream for the tool log: each tool call is
 * two linked events (the invocation and its result), and system errors appear
 * inline as their own events.
 */
export type LogEvent =
  | { kind: "call"; key: string; toolId: string; tool: ToolUse; index: number; timestamp: string | null }
  | { kind: "result"; key: string; toolId: string; tool: ToolUse; index: number; timestamp: string | null }
  | { kind: "system"; key: string; level: string; text: string; timestamp: string | null };

export function buildLogEvents(items: ThreadItem[]): LogEvent[] {
  const events: LogEvent[] = [];
  let n = 0;
  for (const item of items) {
    if (item.role === "system" && isErrorLevel(item.level)) {
      events.push({
        kind: "system",
        key: `sys-${item.uuid}`,
        level: item.level ?? "error",
        text: item.text ?? "",
        timestamp: item.timestamp,
      });
      continue;
    }
    for (const b of item.blocks) {
      if (b.kind === "tool_use" && b.tool_use) {
        n += 1;
        const tool = b.tool_use;
        events.push({
          kind: "call",
          key: `${tool.id}-call`,
          toolId: tool.id,
          tool,
          index: n,
          timestamp: item.timestamp,
        });
        events.push({
          kind: "result",
          key: `${tool.id}-result`,
          toolId: tool.id,
          tool,
          index: n,
          timestamp: item.timestamp,
        });
      }
    }
  }
  return events;
}

function isErrorLevel(level: string | null | undefined): boolean {
  if (!level) return false;
  const l = level.toLowerCase();
  return l === "error" || l === "warning" || l === "warn";
}
