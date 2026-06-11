import type { Thread, ThreadItem, ToolResult } from "../api/types";

/** Append streamed items not already present (main-thread, non-sidechain). */
export function appendFresh(thread: Thread, items: ThreadItem[]): Thread {
  const seen = new Set(thread.items.map((i) => i.uuid));
  const fresh = items.filter((i) => !i.is_sidechain && !seen.has(i.uuid));
  return fresh.length ? { ...thread, items: [...thread.items, ...fresh] } : thread;
}

/** Attach a streamed tool result to its tool_use. */
export function patchResult(thread: Thread, result: ToolResult): Thread {
  let changed = false;
  const items = thread.items.map((item) => {
    const blocks = item.blocks.map((b) => {
      if (b.tool_use && b.tool_use.id === result.tool_use_id && !b.tool_use.result) {
        changed = true;
        return { ...b, tool_use: { ...b.tool_use, result } };
      }
      return b;
    });
    return changed ? { ...item, blocks } : item;
  });
  return changed ? { ...thread, items } : thread;
}
