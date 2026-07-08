/** Long-pressing the send button opens a menu of send modes; "Compact first"
 * runs /compact then queues the reply. */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReplyBox from "./ReplyBox";

vi.mock("../../api/client", () => ({
  api: {
    respondToSession: vi.fn().mockResolvedValue({}),
    queueReply: vi.fn().mockResolvedValue({}),
    getPaneCommands: vi.fn().mockResolvedValue([]),
  },
}));
import { api } from "../../api/client";

const respondToSession = vi.mocked(api.respondToSession);
const queueReply = vi.mocked(api.queueReply);

afterEach(() => {
  respondToSession.mockClear();
  queueReply.mockClear();
});

function renderBox() {
  render(<ReplyBox sessionId="s1" hasPane busy={false} variant="cockpit" showInterrupt={false} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "do the thing" } });
  return screen.getByLabelText("Send");
}

// Long-press = pointerDown, then let the 450ms timer fire.
function longPress(btn: HTMLElement) {
  vi.useFakeTimers();
  fireEvent.pointerDown(btn);
  act(() => vi.advanceTimersByTime(460));
  vi.useRealTimers();
}

// Pretend we're on (or off) a fine-pointer desktop for useIsDesktop().
function setDesktop(on: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: on,
    media: q,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent() {
      return false;
    },
  })) as unknown as typeof window.matchMedia;
}

describe("ReplyBox desktop keyboard", () => {
  afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  it("Enter sends; Shift+Enter is a newline", async () => {
    setDesktop(true);
    render(<ReplyBox sessionId="s1" hasPane busy={false} variant="cockpit" showInterrupt={false} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(respondToSession).not.toHaveBeenCalled();
    fireEvent.keyDown(ta, { key: "Enter" });
    await waitFor(() => expect(respondToSession).toHaveBeenCalledWith("s1", "hi"));
  });

  it("exposes the send-options caret on desktop but not on touch", () => {
    setDesktop(false);
    const { unmount } = render(
      <ReplyBox sessionId="s1" hasPane busy={false} variant="cockpit" showInterrupt={false} />,
    );
    expect(screen.queryByLabelText("Send options")).toBeNull();
    unmount();
    setDesktop(true);
    render(<ReplyBox sessionId="s1" hasPane busy={false} variant="cockpit" showInterrupt={false} />);
    fireEvent.click(screen.getByLabelText("Send options"));
    expect(screen.getByRole("menu")).toBeTruthy();
  });
});

describe("ReplyBox send-options menu", () => {
  it("long-press opens the menu; a plain tap does not", () => {
    const btn = renderBox();
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(btn); // plain tap
    expect(screen.queryByRole("menu")).toBeNull();
    longPress(btn);
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByText("Compact first")).toBeTruthy();
  });

  it("'Compact first' sends /compact then the reply right behind it", async () => {
    const btn = renderBox();
    longPress(btn);
    fireEvent.click(screen.getByText("Compact first"));
    await waitFor(() => expect(respondToSession).toHaveBeenCalledWith("s1", "do the thing"));
    expect(respondToSession).toHaveBeenNthCalledWith(1, "s1", "/compact");
    expect(respondToSession).toHaveBeenNthCalledWith(2, "s1", "do the thing");
    expect(queueReply).not.toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull(); // closed after picking
  });

  it("'Send now' from the menu sends immediately", async () => {
    const btn = renderBox();
    longPress(btn);
    fireEvent.click(screen.getByText("Send now"));
    await waitFor(() => expect(respondToSession).toHaveBeenCalledWith("s1", "do the thing"));
    expect(queueReply).not.toHaveBeenCalled();
  });
});
