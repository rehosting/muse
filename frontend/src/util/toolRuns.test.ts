/** computeToolRuns — the grouping behind focus mode's collapsed tool runs.
 * The invariants that matter: runs form across ITEMS (real transcripts carry
 * ~1 tool call per assistant item), invisible tool_result carriers between
 * calls never break a run, and the run key stays stable while a live tail
 * keeps appending (open/closed UI state hangs off it). */
import { describe, expect, it } from "vitest";
import { computeToolRuns, MIN_RUN_CALLS } from "./toolRuns";
import { makeHiddenUserItem, makeItem, makeToolOnlyItem, makeUserItem } from "./readerFixtures";

// A realistic working stretch: tool call, hidden result carrier, tool call, …
function stretch(n: number, prefix = "s") {
  const items = [];
  for (let i = 0; i < n; i++) {
    items.push(makeToolOnlyItem(`${prefix}${i}`, 1, 1));
    items.push(makeHiddenUserItem(`${prefix}${i}-r`));
  }
  return items;
}

describe("computeToolRuns", () => {
  it("groups consecutive tool-only items, hidden carriers included", () => {
    const items = stretch(4);
    const runs = computeToolRuns(items);
    const run = runs.get("s0")!;
    expect(run).toBeTruthy();
    expect(run.calls).toBe(4);
    expect(run.key).toBe("s0");
    // Every member (carriers too, except a trailing one) resolves to the run.
    expect(runs.get("s3")).toBe(run);
    expect(runs.get("s0-r")).toBe(run);
    // The trailing carrier after the last call is not adopted (a run never
    // ends on an invisible item) — it renders nothing either way.
    expect(runs.get("s3-r")).toBeUndefined();
  });

  it("assistant text, visible user messages, and system lines break runs", () => {
    for (const breaker of [
      makeItem("break"), // assistant with text
      makeUserItem("break"),
      { ...makeItem("break"), role: "system" as const },
    ]) {
      const items = [...stretch(2, "a"), breaker, ...stretch(2, "b")];
      const runs = computeToolRuns(items);
      // 2 + 2 with a visible item between: neither side reaches MIN_RUN_CALLS.
      expect(runs.size).toBe(0);
    }
  });

  it("hidden user items do NOT break a run", () => {
    const items = [
      makeToolOnlyItem("a", 1, 1),
      makeHiddenUserItem("carrier1"),
      makeHiddenUserItem("carrier2", "<system-reminder>noise</system-reminder>"),
      makeToolOnlyItem("b", 1, 1),
      makeToolOnlyItem("c", 1, 0),
    ];
    const run = computeToolRuns(items).get("a")!;
    expect(run.calls).toBe(3);
    expect(run.memberUuids.has("carrier2")).toBe(true);
  });

  it(`runs shorter than ${MIN_RUN_CALLS} calls stay ungrouped`, () => {
    expect(computeToolRuns(stretch(MIN_RUN_CALLS - 1)).size).toBe(0);
  });

  it("items mixing thinking with tool calls join the run", () => {
    const items = [
      makeToolOnlyItem("a", 1, 1, { thinking: true }),
      makeToolOnlyItem("b", 1, 1),
      makeToolOnlyItem("c", 1, 1, { thinking: true }),
    ];
    expect(computeToolRuns(items).get("b")?.calls).toBe(3);
  });

  it("tallies counts, errors, and running state", () => {
    const items = [
      makeToolOnlyItem("a", 2, 2, { errors: 1, name: "Bash" }),
      makeToolOnlyItem("b", 1, 1, { name: "Read" }),
      makeToolOnlyItem("c", 1, 0, { name: "Bash" }), // still running
    ];
    const run = computeToolRuns(items).get("a")!;
    expect(run.calls).toBe(4);
    expect(run.counts[0]).toEqual({ name: "Bash", n: 3 });
    expect(run.errors).toBe(1);
    expect(run.running).toBe(true);
    expect(run.lastTool?.id).toBe("c-t0");
  });

  it("key stays stable while the live tail appends", () => {
    const base = stretch(3);
    const before = computeToolRuns(base).get("s0")!;
    const after = computeToolRuns([...base, makeToolOnlyItem("s99", 1, 0)]).get("s0")!;
    expect(after.key).toBe(before.key);
    expect(after.calls).toBe(4);
  });

  it("an assistant item with a text block never joins a run", () => {
    const items = [...stretch(3, "a"), makeItem("talk", 2, 2)];
    const runs = computeToolRuns(items);
    expect(runs.get("a0")?.calls).toBe(3);
    expect(runs.get("talk")).toBeUndefined();
  });
});
