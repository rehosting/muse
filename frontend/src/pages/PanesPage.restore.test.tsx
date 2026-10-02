/** RestoreBanner: after a reboot the snapshot reports `offer` (Claude windows, none live);
 * the banner opens a per-group review and rebuilds the selected groups. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { getTmuxSnapshot: vi.fn(), restoreTmuxLayout: vi.fn() },
}));
import { api } from "../api/client";
import { RestoreBanner } from "./PanesPage";
import type { LayoutSnapshot } from "../api/types";

function snap(over: Partial<LayoutSnapshot> = {}): LayoutSnapshot {
  return {
    ts: "2026-07-07T00:00:00Z",
    offer: true,
    restorable_count: 3,
    groups: [
      {
        name: "work",
        windows: [
          { window_name: "api", cwd: "/a", command: "claude", kind: "claude", session_id: "s1", live: false },
          { window_name: "ui", cwd: "/b", command: "claude", kind: "claude", session_id: "s2", live: false },
        ],
      },
      {
        name: "exp",
        windows: [
          { window_name: "sh", cwd: "/c", command: "bash", kind: "shell", session_id: null, live: false },
        ],
      },
    ],
    ...over,
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.getTmuxSnapshot).mockReset();
  vi.mocked(api.restoreTmuxLayout).mockReset().mockResolvedValue({
    ok: true, restored: 3, skipped: 0, groups: ["work", "exp"],
  });
});
afterEach(() => localStorage.clear());

describe("RestoreBanner", () => {
  it("stays hidden when the snapshot doesn't offer a restore", async () => {
    vi.mocked(api.getTmuxSnapshot).mockResolvedValue(snap({ offer: false }));
    const { container } = render(<RestoreBanner onRestored={() => {}} />);
    await waitFor(() => expect(api.getTmuxSnapshot).toHaveBeenCalled());
    expect(container.querySelector(".restore-banner")).toBeNull();
  });

  it("shows the banner, reviews groups, and restores the selected ones", async () => {
    vi.mocked(api.getTmuxSnapshot).mockResolvedValue(snap());
    const onRestored = vi.fn();
    render(<RestoreBanner onRestored={onRestored} />);
    // 3 windows across 2 groups aren't running.
    await screen.findByText(/3 windows across 2 groups/);
    fireEvent.click(screen.getByText("Review"));
    expect(screen.getByText("work")).toBeTruthy();
    expect(screen.getByText("exp")).toBeTruthy();
    // Uncheck 'exp' → restore only 'work'.
    const expRow = screen.getByText("exp").closest("label")!;
    fireEvent.click(expRow.querySelector("input")!);
    fireEvent.click(screen.getByText(/Restore selected/));
    await waitFor(() => expect(api.restoreTmuxLayout).toHaveBeenCalledWith(["work"]));
    expect(onRestored).toHaveBeenCalled();
  });

  it("dismiss hides the banner and persists so it won't re-nag", async () => {
    vi.mocked(api.getTmuxSnapshot).mockResolvedValue(snap());
    const { container } = render(<RestoreBanner onRestored={() => {}} />);
    await screen.findByText("Dismiss");
    fireEvent.click(screen.getByText("Dismiss"));
    expect(container.querySelector(".restore-banner")).toBeNull();
    expect(localStorage.getItem("restoreDismissed")).toBe("2026-07-07T00:00:00Z");
  });
});
