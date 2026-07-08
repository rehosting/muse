/** Typing a printable key with nothing editable focused jumps into the composer
 * and captures that first character; modifier combos and already-focused fields
 * are left alone. */
import { fireEvent, render, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { describe, expect, it } from "vitest";
import { useTypeToFocus } from "./typeToFocus";

function Harness({ enabled }: { enabled: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  useTypeToFocus(ref, (ch) => setText((t) => t + ch), enabled);
  return <textarea ref={ref} value={text} onChange={(e) => setText(e.target.value)} />;
}

describe("useTypeToFocus", () => {
  it("focuses the field and appends the typed char", () => {
    render(<Harness enabled />);
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.keyDown(document.body, { key: "a" });
    expect(document.activeElement).toBe(ta);
    expect(ta.value).toBe("a");
  });

  it("ignores modifier combos and non-printable keys", () => {
    render(<Harness enabled />);
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.keyDown(document.body, { key: "c", metaKey: true });
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(ta.value).toBe("");
  });

  it("does nothing when disabled", () => {
    render(<Harness enabled={false} />);
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.keyDown(document.body, { key: "a" });
    expect(ta.value).toBe("");
  });
});
