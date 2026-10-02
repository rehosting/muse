/** useReaderThread — the hook driving panes reader mode, against a mocked API.
 * Pins the exact regressions users hit: head-window fetches, state churn on
 * unchanged polls (scroll jumps), and load-earlier bookkeeping. */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EARLIER_LIMIT, TAIL_LIMIT, useReaderThread } from "./reader";
import { makeItems, makeWindow } from "./readerFixtures";

vi.mock("../api/client", () => ({ api: { getThread: vi.fn() } }));
import { api } from "../api/client";

const getThread = vi.mocked(api.getThread);

afterEach(() => {
  getThread.mockReset();
});

describe("useReaderThread", () => {
  it("fetches the TAIL window explicitly (never the server's head default)", async () => {
    getThread.mockResolvedValue(makeWindow(makeItems(60, 100), 60, 100));
    const { result, unmount } = renderHook(() => useReaderThread("sid", true, 60_000));
    await waitFor(() => expect(result.current.reader).not.toBeNull());
    // anchor:"tail" is load-bearing — the server defaults idle sessions to the
    // HEAD window, which showed the first messages of the whole session.
    expect(getThread).toHaveBeenCalledWith("sid", { limit: TAIL_LIMIT, anchor: "tail" });
    expect(result.current.reader!.items[result.current.reader!.items.length - 1].uuid).toBe("m99");
    unmount();
  });

  it("resolves to an EMPTY thread (not stuck loading) when there's no history", async () => {
    // A brand-new session has no messages: tailSig([]) === "". The reader must still
    // leave the loading state — otherwise PaneCard shows "loading conversation…" forever.
    getThread.mockResolvedValue(makeWindow([], 0, 0));
    const { result, unmount } = renderHook(() => useReaderThread("sid", true, 60_000));
    await waitFor(() => expect(result.current.reader).not.toBeNull());
    expect(result.current.reader!.items).toEqual([]);
    expect(result.current.reader!.total).toBe(0);
    unmount();
  });

  it("shows empty (not a stuck spinner) when the thread 404s, then self-heals", async () => {
    // A brand-new session isn't in the thread index yet, so getThread rejects. The
    // reader must leave the loading state; once the fetch starts succeeding it fills in.
    getThread.mockRejectedValue(new Error("404 Not Found for /api/sessions/x"));
    const { result, unmount } = renderHook(() => useReaderThread("sid", true, 30));
    await waitFor(() => expect(result.current.reader).not.toBeNull());
    expect(result.current.reader!.items).toEqual([]);
    // Session gets indexed → the next poll returns real content.
    getThread.mockResolvedValue(makeWindow(makeItems(0, 3), 0, 3));
    await waitFor(() => expect(result.current.reader!.items).toHaveLength(3));
    unmount();
  });

  it("keeps state IDENTICAL across polls when nothing changed", async () => {
    getThread.mockResolvedValue(makeWindow(makeItems(0, 40), 0, 40));
    const { result, unmount } = renderHook(() => useReaderThread("sid", true, 30));
    await waitFor(() => expect(result.current.reader).not.toBeNull());
    const first = result.current.reader;
    await waitFor(() => expect(getThread.mock.calls.length).toBeGreaterThanOrEqual(3));
    // Same object — no setState happened, so React re-rendered nothing and the
    // scroll position physically cannot jump. (The original implementation
    // replaced state every poll; on idle sessions that threw you to the top.)
    expect(result.current.reader).toBe(first);
    unmount();
  });

  it("appends when the conversation advances, keeping history identity", async () => {
    getThread.mockResolvedValue(makeWindow(makeItems(0, 40), 0, 40));
    const { result, unmount } = renderHook(() => useReaderThread("sid", true, 30));
    await waitFor(() => expect(result.current.reader).not.toBeNull());
    const firstItem = result.current.reader!.items[0];
    getThread.mockResolvedValue(makeWindow(makeItems(2, 42), 2, 42));
    await waitFor(() => expect(result.current.reader!.items[result.current.reader!.items.length - 1].uuid).toBe("m41"));
    expect(result.current.reader!.items[0]).toBe(firstItem);
    expect(result.current.reader!.items).toHaveLength(42);
    unmount();
  });

  it("loadEarlier prepends the window just before what we have", async () => {
    getThread.mockResolvedValue(makeWindow(makeItems(60, 100), 60, 100));
    const { result, unmount } = renderHook(() => useReaderThread("sid", true, 60_000));
    await waitFor(() => expect(result.current.reader).not.toBeNull());

    getThread.mockResolvedValue(makeWindow(makeItems(0, 60), 0, 100));
    let prepended = false;
    await act(async () => {
      prepended = await result.current.loadEarlier();
    });
    expect(prepended).toBe(true);
    expect(getThread).toHaveBeenLastCalledWith("sid", { limit: EARLIER_LIMIT, before: 60 });
    expect(result.current.reader!.start).toBe(0);
    expect(result.current.reader!.items.map((i) => i.uuid)).toEqual(
      makeItems(0, 100).map((i) => i.uuid),
    );
    unmount();
  });

  it("loadEarlier is a no-op at the very start of the thread", async () => {
    getThread.mockResolvedValue(makeWindow(makeItems(0, 30), 0, 30));
    const { result, unmount } = renderHook(() => useReaderThread("sid", true, 60_000));
    await waitFor(() => expect(result.current.reader).not.toBeNull());
    const calls = getThread.mock.calls.length;
    let prepended = true;
    await act(async () => {
      prepended = await result.current.loadEarlier();
    });
    expect(prepended).toBe(false);
    expect(getThread.mock.calls.length).toBe(calls); // no fetch fired
    unmount();
  });

  it("resets when the session id changes", async () => {
    getThread.mockResolvedValue(makeWindow(makeItems(0, 40), 0, 40));
    const { result, rerender, unmount } = renderHook(
      ({ sid }: { sid: string | null }) => useReaderThread(sid, true, 60_000),
      { initialProps: { sid: "a" as string | null } },
    );
    await waitFor(() => expect(result.current.reader).not.toBeNull());
    getThread.mockResolvedValue(makeWindow(makeItems(500, 540), 500, 540));
    rerender({ sid: "b" });
    await waitFor(() => expect(result.current.reader?.start).toBe(500));
    unmount();
  });
});
