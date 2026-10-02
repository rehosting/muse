/** FilePathLink: a path is a click target that fires the global "muse:view-file"
 * event; an unknown path falls back to plain text (no button). */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FilePathLink from "./FilePathLink";
import { VIEW_FILE_EVENT } from "../util/openFile";

describe("FilePathLink", () => {
  it("dispatches muse:view-file with the path on click", () => {
    const spy = vi.fn();
    window.addEventListener(VIEW_FILE_EVENT, spy);
    render(<FilePathLink path="/home/luke/.claude/plans/x.md" />);
    fireEvent.click(screen.getByRole("button"));
    window.removeEventListener(VIEW_FILE_EVENT, spy);
    expect(spy).toHaveBeenCalledTimes(1);
    const ev = spy.mock.calls[0][0] as CustomEvent<{ path: string }>;
    expect(ev.detail.path).toBe("/home/luke/.claude/plans/x.md");
  });

  it("renders plain text (no button) when the path is unknown", () => {
    render(<FilePathLink path={undefined} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("(unknown)")).toBeTruthy();
  });
});
