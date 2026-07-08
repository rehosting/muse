import type { ThreadItem, ToolUse } from "../api/types";
import { classifyUser, toolTitle } from "../components/ccInline";

/** A run of consecutive tool-only activity, collapsed to one row in focus mode.
 * Keyed by the FIRST member's uuid, which stays stable while the tail of a live
 * run keeps growing — so open/closed state survives polls. */
export interface ToolRun {
  key: string;
  firstUuid: string;
  memberUuids: Set<string>;
  calls: number;
  /** Per-tool tally (display names), most frequent first. */
  counts: { name: string; n: number }[];
  errors: number;
  /** Any call still without a result — the run is live. */
  running: boolean;
  /** The newest call — shown as the "current step" while the run is collapsed. */
  lastTool: ToolUse | null;
}

/** Shorter runs read fine as-is; grouping only pays off past this many calls. */
export const MIN_RUN_CALLS = 3;

// Tool-only assistant item: every block is a tool call or the thinking that
// precedes one. (Thinking is folded into the run — focus mode reduces it to a
// stub line anyway, and expanding the run brings the stubs back.)
function isToolish(item: ThreadItem): boolean {
  if (item.role !== "assistant" || item.blocks.length === 0) return false;
  let tools = 0;
  for (const b of item.blocks) {
    if (b.kind === "tool_use" && b.tool_use) tools++;
    else if (b.kind !== "thinking") return false;
  }
  return tools > 0;
}

// Items that render nothing today (tool_result carriers, harness wrappers) must
// not break a run — they sit between every pair of tool calls in a transcript.
function isTransparent(item: ThreadItem): boolean {
  if (item.role !== "user") return false;
  if (!item.text) return true;
  return classifyUser(item.text).kind === "hidden";
}

/**
 * Group consecutive tool-only items into runs. Returns a map from EVERY member
 * item's uuid to its (shared) run, so rendering is one O(1) lookup per item.
 * Anything visible that isn't a tool call (assistant text, a real user message,
 * a system line) closes the current run.
 */
export function computeToolRuns(
  items: ThreadItem[],
  minCalls: number = MIN_RUN_CALLS,
): Map<string, ToolRun> {
  const runs = new Map<string, ToolRun>();
  let members: ThreadItem[] = [];
  // Transparent items are only adopted into the run once another tool call
  // follows them — a run never ends on an invisible carrier.
  let pending: ThreadItem[] = [];

  const close = () => {
    pending = [];
    if (members.length === 0) return;
    const tools: ToolUse[] = [];
    for (const it of members) {
      for (const b of it.blocks) if (b.kind === "tool_use" && b.tool_use) tools.push(b.tool_use);
    }
    if (tools.length >= minCalls) {
      const tally = new Map<string, number>();
      let errors = 0;
      let running = false;
      for (const t of tools) {
        const name = toolTitle(t.name);
        tally.set(name, (tally.get(name) ?? 0) + 1);
        if (!t.result) running = true;
        else if (t.result.is_error) errors++;
      }
      const run: ToolRun = {
        key: members[0].uuid,
        firstUuid: members[0].uuid,
        memberUuids: new Set(members.map((m) => m.uuid)),
        calls: tools.length,
        counts: [...tally.entries()]
          .map(([name, n]) => ({ name, n }))
          .sort((a, b) => b.n - a.n),
        errors,
        running,
        lastTool: tools[tools.length - 1] ?? null,
      };
      for (const uuid of run.memberUuids) runs.set(uuid, run);
    }
    members = [];
  };

  for (const item of items) {
    if (isToolish(item)) {
      members.push(...pending, item);
      pending = [];
    } else if (members.length > 0 && isTransparent(item)) {
      pending.push(item);
    } else if (!isTransparent(item)) {
      close();
    }
  }
  close();
  return runs;
}
