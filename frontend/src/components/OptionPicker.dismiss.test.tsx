/** OptionPicker's dismiss control: present only when the caller supplies a
 * handler, and never rendered as a chip (tapping it must not answer anything). */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OptionPicker from "./OptionPicker";

const pending = {
  session_id: "sid",
  source: "permission",
  available: true,
  prompt: "Proceed?",
  options: [
    { id: "1", label: "Yes", description: "do it", kind: "menu" },
    { id: "2", label: "No", description: null, kind: "menu" },
  ],
  current_index: 0,
  fingerprint: "fp",
  remaining_questions: 0,
  pane_id: "%1",
  in_tmux: true,
  reason: null,
} as never;

describe("OptionPicker dismiss", () => {
  it("fires onDismiss without selecting an option", () => {
    const onDismiss = vi.fn();
    const onSelect = vi.fn();
    render(
      <OptionPicker pending={pending} sending={false} onSelect={onSelect} onDismiss={onDismiss} />,
    );
    fireEvent.click(screen.getByLabelText("Dismiss options"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("omits the control when no handler is given", () => {
    render(<OptionPicker pending={pending} sending={false} onSelect={() => {}} />);
    expect(screen.queryByLabelText("Dismiss options")).toBeNull();
  });
});
