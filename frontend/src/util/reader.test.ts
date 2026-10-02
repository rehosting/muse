/** Reader data layer — the pure merge/signature logic behind panes reader mode.
 * Every case here is a bug we actually shipped: head-window data, scroll jumps
 * from identity churn, updates that never re-triggered. */
import { describe, expect, it } from "vitest";
import { mergeTail, prependEarlier, tailSig } from "./reader";
import { makeItem, makeItems, makeWindow } from "./readerFixtures";

describe("tailSig", () => {
  it("is stable for identical content (poll must be a no-op)", () => {
    expect(tailSig(makeItems(0, 5))).toBe(tailSig(makeItems(0, 5)));
  });

  it("changes when a new message arrives", () => {
    expect(tailSig(makeItems(0, 5))).not.toBe(tailSig(makeItems(0, 6)));
  });

  it("changes when a tool result lands on an EXISTING item", () => {
    // Same uuids, same length — only the result attached. items.length-style
    // change detection misses this entirely (the bug that froze the tail).
    const before = [makeItem("a"), makeItem("b", 0, 1)];
    const after = [makeItem("a"), makeItem("b", 1, 1)];
    expect(tailSig(before)).not.toBe(tailSig(after));
  });

  it("changes when the window slides even at constant length", () => {
    // Tail windows are ALWAYS `limit` long — length can never signal progress.
    expect(tailSig(makeItems(0, 40))).not.toBe(tailSig(makeItems(1, 41)));
  });
});

describe("mergeTail", () => {
  it("adopts the window on first load", () => {
    const win = makeWindow(makeItems(60, 100), 60, 100);
    const s = mergeTail(null, win);
    expect(s.start).toBe(60);
    expect(s.total).toBe(100);
    expect(s.items.map((i) => i.uuid)).toEqual(win.items.map((i) => i.uuid));
  });

  it("appends new tail items while keeping accumulated history by identity", () => {
    const prev = mergeTail(null, makeWindow(makeItems(10, 50), 10, 50));
    // Two new messages: the 40-item window now covers [12, 52).
    const s = mergeTail(prev, makeWindow(makeItems(12, 52), 12, 52));
    expect(s.start).toBe(10);
    expect(s.items.map((i) => i.uuid)).toEqual(makeItems(10, 52).map((i) => i.uuid));
    // History kept by OBJECT IDENTITY — replacing it re-renders the whole
    // conversation and yanks the scroll position (the jump-to-top bug).
    expect(s.items[0]).toBe(prev.items[0]);
    expect(s.items[1]).toBe(prev.items[1]);
  });

  it("swaps overlap items for the fresh copies (tool results mutate in place)", () => {
    const prev = mergeTail(null, makeWindow(makeItems(0, 40), 0, 40));
    const fresh = makeWindow(makeItems(0, 40), 0, 40);
    const s = mergeTail(prev, fresh);
    // Full overlap → everything comes from the fresh window.
    expect(s.items[39]).toBe(fresh.items[39]);
    expect(s.items).toHaveLength(40);
  });

  it("resets to the new tail when the conversation advanced past our window", () => {
    const prev = mergeTail(null, makeWindow(makeItems(0, 40), 0, 40));
    // 100 messages later: the tail window no longer touches what we have.
    const win = makeWindow(makeItems(100, 140), 100, 140);
    const s = mergeTail(prev, win);
    expect(s.start).toBe(100);
    expect(s.items.map((i) => i.uuid)).toEqual(win.items.map((i) => i.uuid));
  });

  it("keeps prepended history across subsequent tail merges", () => {
    // Accumulated [0, 100) after a load-earlier, then a tail poll for [62, 102).
    let s = mergeTail(null, makeWindow(makeItems(60, 100), 60, 100));
    s = prependEarlier(s, makeWindow(makeItems(0, 60), 0, 100));
    const s2 = mergeTail(s, makeWindow(makeItems(62, 102), 62, 102));
    expect(s2.start).toBe(0);
    expect(s2.items.map((i) => i.uuid)).toEqual(makeItems(0, 102).map((i) => i.uuid));
    expect(s2.items[0]).toBe(s.items[0]); // history identity intact
  });
});

describe("prependEarlier", () => {
  it("prepends the earlier window and keeps existing items by identity", () => {
    const prev = mergeTail(null, makeWindow(makeItems(60, 100), 60, 100));
    const s = prependEarlier(prev, makeWindow(makeItems(0, 60), 0, 100));
    expect(s.start).toBe(0);
    expect(s.items.map((i) => i.uuid)).toEqual(makeItems(0, 100).map((i) => i.uuid));
    expect(s.items[60]).toBe(prev.items[0]);
  });
});
