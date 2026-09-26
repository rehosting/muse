/** usePendingOptions — dismissing a misparsed menu. Screen parsing has false
 * positives, so the row must be hideable without answering; and the dismissal
 * must not swallow whatever the session genuinely asks next. */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePendingOptions } from "./usePendingOptions";

vi.mock("../api/client", () => ({
  api: { getPendingOptions: vi.fn(), selectPendingOption: vi.fn() },
}));
import { api } from "../api/client";

const getPendingOptions = vi.mocked(api.getPendingOptions);

const menu = (fingerprint: string, prompt: string) =>
  ({
    session_id: "sid",
    source: "permission",
    available: true,
    prompt,
    options: [{ id: "1", label: "Yes", description: null, kind: "menu" }],
    current_index: 0,
    fingerprint,
    remaining_questions: 0,
    pane_id: "%1",
    in_tmux: true,
    reason: null,
  }) as never;

afterEach(() => getPendingOptions.mockReset());

describe("usePendingOptions dismiss", () => {
  it("hides the current menu and keeps it hidden across polls", async () => {
    getPendingOptions.mockResolvedValue(menu("fp-a", "Proceed?"));
    const { result, unmount } = renderHook(() => usePendingOptions("sid", true));
    await waitFor(() => expect(result.current.pending).not.toBeNull());

    act(() => result.current.dismiss());
    expect(result.current.pending).toBeNull();

    // The same false menu is still on screen; re-polling must not resurrect it.
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.pending).toBeNull();
    unmount();
  });

  it("shows the NEXT prompt — dismissal is keyed to the fingerprint, not the session", async () => {
    getPendingOptions.mockResolvedValue(menu("fp-a", "Proceed?"));
    const { result, unmount } = renderHook(() => usePendingOptions("sid", true));
    await waitFor(() => expect(result.current.pending).not.toBeNull());
    act(() => result.current.dismiss());
    expect(result.current.pending).toBeNull();

    getPendingOptions.mockResolvedValue(menu("fp-b", "Push the branch?"));
    await act(async () => {
      await result.current.refresh();
    });
    await waitFor(() => expect(result.current.pending).not.toBeNull());
    expect(result.current.pending!.prompt).toBe("Push the branch?");
    unmount();
  });
});
