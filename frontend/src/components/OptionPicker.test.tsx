/** OptionPicker: renders the choices a session is presenting, plus (new) the
 * long-form `detail` (a plan body) so it's reviewable right where it's answered. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OptionPicker from "./OptionPicker";
import type { PendingOptions } from "../api/types";

function pending(over: Partial<PendingOptions>): PendingOptions {
  return {
    session_id: "s",
    source: "tool_question",
    available: true,
    prompt: "Review the plan, then choose:",
    detail: "",
    options: [
      { id: "1", label: "Yes, proceed", description: null, kind: "menu" },
      { id: "2", label: "No, keep planning", description: null, kind: "menu" },
    ],
    current_index: null,
    fingerprint: "fp",
    remaining_questions: 0,
    pane_id: "%1",
    in_tmux: true,
    reason: null,
    ...over,
  };
}

describe("OptionPicker", () => {
  it("renders the prompt and fires onSelect with the option id", () => {
    const onSelect = vi.fn();
    render(<OptionPicker pending={pending({})} sending={false} onSelect={onSelect} />);
    expect(screen.getByText("Review the plan, then choose:")).toBeTruthy();
    fireEvent.click(screen.getByText("Yes, proceed"));
    expect(onSelect).toHaveBeenCalledWith("1");
  });

  it("renders the plan detail as reviewable content when present", () => {
    render(
      <OptionPicker
        pending={pending({ detail: "## Plan\n\nFirst do the thing, then the other thing." })}
        sending={false}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText(/First do the thing/)).toBeTruthy();
  });

  it("toggles the detail between capped and full height", () => {
    render(
      <OptionPicker
        pending={pending({ detail: "line one\nline two" })}
        sending={false}
        onSelect={() => {}}
      />,
    );
    const toggle = screen.getByText("▾ read full plan");
    fireEvent.click(toggle);
    expect(screen.getByText("▴ collapse")).toBeTruthy();
  });

  it("omits the detail block when there's no detail", () => {
    render(<OptionPicker pending={pending({ detail: "" })} sending={false} onSelect={() => {}} />);
    expect(screen.queryByText(/read full plan/)).toBeNull();
  });
});
