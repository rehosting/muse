/** Starred as a VIRTUAL GROUP in the terminal view: selectable wherever a tmux
 * session is (rail, deck section switcher), scoped to the windows you've starred with
 * the existing ☆/★ pins, persisted through the same panesGroup key, and never leaving
 * a silently empty list. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: {
    getTmuxLayout: vi.fn(), getPaneScreen: vi.fn(), getTmuxSnapshot: vi.fn(),
    listProfiles: vi.fn(), getWindowCleanup: vi.fn(), sendToPane: vi.fn(),
    sendPaneKey: vi.fn(), cyclePaneMode: vi.fn(), closeTmuxWindow: vi.fn(),
    createTmuxSession: vi.fn(), deleteTmuxSession: vi.fn(), moveTmuxWindow: vi.fn(),
    renameTmuxSession: vi.fn(), renameTmuxWindow: vi.fn(), restoreTmuxLayout: vi.fn(),
    launchProfile: vi.fn(), launchCodexFromSession: vi.fn(), getPendingOptions: vi.fn(),
    selectPendingOption: vi.fn(), getThread: vi.fn(),
  },
}));
import { api } from "../api/client";
import PanesPage, { DeckGroupSwitcher, STARRED_GROUP } from "./PanesPage";

const pane = (over = {}) => ({
  pane_id: "%1", session_name: "main", window_index: 0, window_name: "alpha",
  window_active: true, pane_index: 0, pane_active: true, command: "claude",
  cwd: "/w", title: "", session_attached: true, last_activity: 0, window_id: "@1",
  preview: "", preview_tail: "x", muse_session_id: "sid-1", status: "idle",
  attention: "", options: [], capabilities: {}, provider: "claude", mode: "default",
  context_pct: 10, queued: 0, ...over,
});

const mount = () =>
  render(
    <MemoryRouter initialEntries={["/panes"]}>
      <PanesPage />
    </MemoryRouter>,
  );

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.getTmuxLayout).mockResolvedValue({
    available: true, reason: null,
    panes: [
      pane(),
      pane({ pane_id: "%2", window_index: 1, window_name: "beta", window_id: "@2", muse_session_id: "sid-2" }),
    ],
  } as never);
  vi.mocked(api.listProfiles).mockResolvedValue([] as never);
  vi.mocked(api.getTmuxSnapshot).mockResolvedValue(
    { ts: null, groups: [], offer: false, restorable_count: 0 } as never,
  );
  vi.mocked(api.getPendingOptions).mockResolvedValue({ available: false } as never);
  vi.mocked(api.getPaneScreen).mockResolvedValue({ text: "", options: [] } as never);
});

describe("starred-only filter", () => {
  it("hides unstarred windows when enabled, and restores them when off", async () => {
    localStorage.setItem("panesPins", JSON.stringify(["main:0"]));
    mount();
    await waitFor(() => expect(screen.getByText("alpha")).toBeTruthy());
    expect(screen.getByText("beta")).toBeTruthy();

    fireEvent.click(screen.getByTitle(/Show only starred|Showing starred only/));
    await waitFor(() => expect(screen.queryByText("beta")).toBeNull());
    expect(screen.getByText("alpha")).toBeTruthy(); // the starred one stays

    fireEvent.click(screen.getByTitle(/Show only starred|Showing starred only/));
    await waitFor(() => expect(screen.getByText("beta")).toBeTruthy());
  });

  it("survives a reload", async () => {
    localStorage.setItem("panesGroup", STARRED_GROUP); // selected like any section
    localStorage.setItem("panesPins", JSON.stringify(["main:0"]));
    mount();
    await waitFor(() => expect(screen.getByText("alpha")).toBeTruthy());
    expect(screen.queryByText("beta")).toBeNull();
  });

  it("explains an empty list rather than showing nothing", async () => {
    localStorage.setItem("panesGroup", STARRED_GROUP); // selected, but nothing starred
    mount();
    await waitFor(() => expect(screen.getByText(/Nothing starred yet/)).toBeTruthy());
    // ...and offers the way out.
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    await waitFor(() => expect(screen.getByText("alpha")).toBeTruthy());
  });
});


describe("starred survives the stale-group guard", () => {
  it("is not cleared by the fallback that drops vanished tmux groups", async () => {
    // ★ Starred has no tmux session behind it; the guard that resets a killed group
    // to All used to discard it the instant it was selected.
    localStorage.setItem("panesGroup", STARRED_GROUP);
    localStorage.setItem("panesPins", JSON.stringify(["main:0"]));
    mount();
    await waitFor(() => expect(screen.getByText("alpha")).toBeTruthy());
    await new Promise((r) => setTimeout(r, 50)); // let the guard effect run
    expect(screen.queryByText("beta")).toBeNull();
    expect(localStorage.getItem("panesGroup")).toBe(STARRED_GROUP);
  });
});

describe("DeckGroupSwitcher", () => {
  it("offers Starred alongside the real sections and selects it", () => {
    const onSwitch = vi.fn();
    render(
      <DeckGroupSwitcher group={null} groups={["main", "features"]} starredCount={3} onSwitch={onSwitch} />,
    );
    fireEvent.click(screen.getByTitle("Switch section"));
    fireEvent.click(screen.getByText("Starred"));
    expect(onSwitch).toHaveBeenCalledWith(STARRED_GROUP);
  });

  it("labels the chip ★ Starred rather than the raw sentinel", () => {
    render(
      <DeckGroupSwitcher group={STARRED_GROUP} groups={["main"]} starredCount={2} onSwitch={() => {}} />,
    );
    expect(screen.getByTitle("Switch section").textContent).toContain("★ Starred");
    expect(screen.getByTitle("Switch section").textContent).not.toContain(":starred");
  });

  it("hides the Starred entry when nothing is starred", () => {
    render(<DeckGroupSwitcher group={null} groups={["main"]} starredCount={0} onSwitch={() => {}} />);
    fireEvent.click(screen.getByTitle("Switch section"));
    expect(screen.queryByText("Starred")).toBeNull();
  });
});
