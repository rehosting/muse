/** QueueChips gates polling on `enabled` so 8+ deck cards don't each poll the
 * queue every 5s. Disabled = no fetches; flipping on fetches immediately. */
import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import QueueChips from "./QueueChips";

vi.mock("../../api/client", () => ({ api: { getQueue: vi.fn() } }));
import { api } from "../../api/client";

const getQueue = vi.mocked(api.getQueue);

afterEach(() => getQueue.mockReset());

describe("QueueChips polling gate", () => {
  it("does not fetch while disabled", () => {
    getQueue.mockResolvedValue({ items: [], hold_reason: null });
    render(<QueueChips sessionId="s" enabled={false} />);
    expect(getQueue).not.toHaveBeenCalled();
  });

  it("fetches immediately once enabled", async () => {
    getQueue.mockResolvedValue({ items: [], hold_reason: null });
    const { rerender } = render(<QueueChips sessionId="s" enabled={false} />);
    expect(getQueue).not.toHaveBeenCalled();
    rerender(<QueueChips sessionId="s" enabled={true} />);
    await waitFor(() => expect(getQueue).toHaveBeenCalledWith("s"));
  });
});
