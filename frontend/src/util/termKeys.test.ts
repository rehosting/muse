import { describe, expect, it } from "vitest";
import { classifyKey, type KeyEventLike } from "./termKeys";

function ev(key: string, mods: Partial<KeyEventLike> = {}): KeyEventLike {
  return { key, ctrlKey: false, altKey: false, metaKey: false, shiftKey: false, ...mods };
}

describe("classifyKey", () => {
  it("buffers plain printable characters", () => {
    expect(classifyKey(ev("a"))).toEqual({ kind: "char", ch: "a" });
    expect(classifyKey(ev("!"))).toEqual({ kind: "char", ch: "!" });
    expect(classifyKey(ev(" "))).toEqual({ kind: "char", ch: " " });
    expect(classifyKey(ev("A", { shiftKey: true }))).toEqual({ kind: "char", ch: "A" });
  });

  it("sends named keys to the session", () => {
    expect(classifyKey(ev("Enter"))).toEqual({ kind: "key", key: "enter" });
    expect(classifyKey(ev("Backspace"))).toEqual({ kind: "key", key: "backspace" });
    expect(classifyKey(ev("Escape"))).toEqual({ kind: "key", key: "escape" });
    expect(classifyKey(ev("Tab"))).toEqual({ kind: "key", key: "tab" });
    expect(classifyKey(ev("Delete"))).toEqual({ kind: "key", key: "delete" });
    expect(classifyKey(ev("ArrowUp"))).toEqual({ kind: "key", key: "up" });
    expect(classifyKey(ev("ArrowRight"))).toEqual({ kind: "key", key: "right" });
  });

  it("prefixes ctrl / alt / shift", () => {
    expect(classifyKey(ev("c", { ctrlKey: true }))).toEqual({ kind: "key", key: "c-c" });
    expect(classifyKey(ev("r", { ctrlKey: true }))).toEqual({ kind: "key", key: "c-r" });
    expect(classifyKey(ev("b", { altKey: true }))).toEqual({ kind: "key", key: "m-b" });
    expect(classifyKey(ev("Tab", { shiftKey: true }))).toEqual({ kind: "key", key: "s-tab" });
  });

  it("routes Alt+arrows to pane nav and Alt+Esc to back", () => {
    expect(classifyKey(ev("ArrowLeft", { altKey: true }))).toEqual({ kind: "prevPane" });
    expect(classifyKey(ev("ArrowRight", { altKey: true }))).toEqual({ kind: "nextPane" });
    expect(classifyKey(ev("Escape", { altKey: true }))).toEqual({ kind: "back" });
  });

  it("leaves browser/OS shortcuts alone", () => {
    expect(classifyKey(ev("w", { ctrlKey: true }))).toEqual({ kind: "ignore" }); // close tab
    expect(classifyKey(ev("t", { ctrlKey: true }))).toEqual({ kind: "ignore" }); // new tab
    expect(classifyKey(ev("n", { ctrlKey: true }))).toEqual({ kind: "ignore" });
    expect(classifyKey(ev("l", { metaKey: true }))).toEqual({ kind: "ignore" }); // Cmd+L
    expect(classifyKey(ev("a", { metaKey: true }))).toEqual({ kind: "ignore" });
    expect(classifyKey(ev("F5"))).toEqual({ kind: "ignore" }); // refresh / unmapped
  });

  it("still sends non-tab ctrl letters", () => {
    // Ctrl-L (clear) and Ctrl-A (line start) are terminal controls, not browser-reserved here.
    expect(classifyKey(ev("l", { ctrlKey: true }))).toEqual({ kind: "key", key: "c-l" });
    expect(classifyKey(ev("a", { ctrlKey: true }))).toEqual({ kind: "key", key: "c-a" });
  });
});
