/** RemoveDialog + MoveMenu Remove row: closing a session, with an opt-in cleanup checkbox
 * that shows the matching profile's exact teardown command. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MoveMenu, RemoveDialog } from "./PanesPage";

describe("MoveMenu Remove row", () => {
  it("renders a Remove row only when onRemove is provided, and fires it", () => {
    const onRemove = vi.fn();
    render(
      <MoveMenu
        groups={["a", "b"]}
        currentSession="a"
        onMove={() => {}}
        onMoveNew={() => {}}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByTitle("Move to a group"));
    fireEvent.click(screen.getByText("Remove session…"));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it("omits the Remove row without onRemove", () => {
    render(<MoveMenu groups={["a"]} currentSession="a" onMove={() => {}} onMoveNew={() => {}} />);
    fireEvent.click(screen.getByTitle("Move to a group"));
    expect(screen.queryByText("Remove session…")).toBeNull();
  });
});

describe("RemoveDialog", () => {
  const withCleanup = {
    windowId: "@7",
    name: "stuff",
    cleanup: { profile: "igloo dev", command: "./do_worktree.sh remove stuff" },
  };

  it("shows the cleanup command + a default-on checkbox and confirms with cleanup=true", () => {
    const onConfirm = vi.fn();
    render(<RemoveDialog pending={withCleanup} onCancel={() => {}} onConfirm={onConfirm} />);
    expect(screen.getByText("./do_worktree.sh remove stuff")).toBeTruthy();
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it("unchecking cleanup confirms with cleanup=false", () => {
    const onConfirm = vi.fn();
    render(<RemoveDialog pending={withCleanup} onCancel={() => {}} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("no match → no checkbox, confirms with cleanup=false", () => {
    const onConfirm = vi.fn();
    render(
      <RemoveDialog
        pending={{ windowId: "@9", name: "plain", cleanup: null }}
        onCancel={() => {}}
        onConfirm={onConfirm}
      />,
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("renders nothing when there's no pending removal", () => {
    const { container } = render(
      <RemoveDialog pending={null} onCancel={() => {}} onConfirm={() => {}} />,
    );
    expect(container.querySelector(".remove-dialog")).toBeNull();
  });
});
