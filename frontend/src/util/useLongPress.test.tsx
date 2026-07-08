/** useLongPress: fires after a touch hold, ignores mouse, cancels on move, and
 * swallows the click that follows a fired long-press. */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLongPress } from "./useLongPress";

// Mirrors real usage: long-press handlers live on a CHILD; the parent owns the
// tap action (open/select). The hook's onClick stops a fired hold from bubbling.
function Harness({ onLong, onParentClick }: { onLong: () => void; onParentClick: () => void }) {
  const lp = useLongPress(onLong, { ms: 500, moveTol: 10 });
  return (
    <div data-testid="parent" onClick={onParentClick}>
      <span data-testid="t" {...lp}>
        hold me
      </span>
    </div>
  );
}

afterEach(() => vi.useRealTimers());

function press(el: HTMLElement, opts: Record<string, unknown> = {}) {
  fireEvent.pointerDown(el, { pointerType: "touch", clientX: 0, clientY: 0, ...opts });
}

describe("useLongPress", () => {
  it("fires onLongPress after the hold on touch, then swallows the bubbled click", () => {
    vi.useFakeTimers();
    const onLong = vi.fn();
    const onParentClick = vi.fn();
    render(<Harness onLong={onLong} onParentClick={onParentClick} />);
    const el = screen.getByTestId("t");
    press(el);
    act(() => vi.advanceTimersByTime(510));
    expect(onLong).toHaveBeenCalledTimes(1);
    // The tap that follows a fired hold must not reach the parent's action.
    fireEvent.click(el);
    expect(onParentClick).not.toHaveBeenCalled();
  });

  it("lets a normal tap (no hold) bubble to the parent", () => {
    const onParentClick = vi.fn();
    render(<Harness onLong={() => {}} onParentClick={onParentClick} />);
    fireEvent.click(screen.getByTestId("t"));
    expect(onParentClick).toHaveBeenCalledTimes(1);
  });

  it("does not fire for a mouse pointer (desktop uses the ✎ affordance)", () => {
    vi.useFakeTimers();
    const onLong = vi.fn();
    render(<Harness onLong={onLong} onParentClick={() => {}} />);
    press(screen.getByTestId("t"), { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(1000));
    expect(onLong).not.toHaveBeenCalled();
  });

  it("cancels when the finger moves past the tolerance (a scroll, not a hold)", () => {
    vi.useFakeTimers();
    const onLong = vi.fn();
    render(<Harness onLong={onLong} onParentClick={() => {}} />);
    const el = screen.getByTestId("t");
    press(el);
    fireEvent.pointerMove(el, { clientX: 40, clientY: 0 });
    act(() => vi.advanceTimersByTime(1000));
    expect(onLong).not.toHaveBeenCalled();
  });
});
