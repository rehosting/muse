/** Smoke render of the whole /panes page against a mocked API. The page is large
 * and mostly untested end-to-end; a crash here white-screens the route with no
 * server-side symptom (the shell and the API both still answer 200). */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: {
    getTmuxLayout: vi.fn(),
    getPaneScreen: vi.fn(),
    getTmuxSnapshot: vi.fn(),
    listProfiles: vi.fn(),
    getWindowCleanup: vi.fn(),
    sendToPane: vi.fn(),
    sendPaneKey: vi.fn(),
    cyclePaneMode: vi.fn(),
    closeTmuxWindow: vi.fn(),
    createTmuxSession: vi.fn(),
    deleteTmuxSession: vi.fn(),
    moveTmuxWindow: vi.fn(),
    renameTmuxSession: vi.fn(),
    renameTmuxWindow: vi.fn(),
    restoreTmuxLayout: vi.fn(),
    launchProfile: vi.fn(),
    launchCodexFromSession: vi.fn(),
    getPendingOptions: vi.fn(),
    selectPendingOption: vi.fn(),
    getThread: vi.fn(),
  },
}));
import { api } from "../api/client";
import PanesPage from "./PanesPage";

const pane = (over = {}) => ({
  pane_id: "%1",
  session_name: "main",
  window_index: 0,
  window_name: "muse",
  window_active: true,
  pane_index: 0,
  pane_active: true,
  command: "claude",
  cwd: "/home/luke/workspace/muse",
  title: "",
  session_attached: true,
  last_activity: 0,
  window_id: "@1",
  preview: "hello",
  muse_session_id: "sid-1",
  status: "idle",
  attention: null,
  options: [],
  capabilities: { reader: true, rich_reply: true, queue_replies: true },
  provider: "claude",
  mode: "default",
  context_pct: 12,
  queued: 0,
  preview_tail: "hello",
  ...over,
});

describe("PanesPage smoke", () => {
  it("renders the pane list without throwing", async () => {
    vi.mocked(api.getTmuxLayout).mockResolvedValue({
      available: true,
      reason: null,
      panes: [pane(), pane({ pane_id: "%2", window_name: "iglootodo", muse_session_id: "sid-2" })],
    } as never);
    vi.mocked(api.listProfiles).mockResolvedValue([] as never);
    vi.mocked(api.getTmuxSnapshot).mockResolvedValue({
      ts: null, groups: [], offer: false, restorable_count: 0,
    } as never);
    vi.mocked(api.getPendingOptions).mockResolvedValue({ available: false } as never);
    vi.mocked(api.getPaneScreen).mockResolvedValue({ text: "", options: [] } as never);

    render(
      <MemoryRouter initialEntries={["/panes"]}>
        <PanesPage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(api.getTmuxLayout).toHaveBeenCalled());
    // Not asserting specific chrome (the task list regroups); the point is that the
    // route mounts with real-shaped data instead of throwing and white-screening.
    await waitFor(() => expect(screen.queryByText(/No tmux panes found/)).toBeNull());
  });

  it("survives a layout payload whose panes omit optional fields", async () => {
    // The server has shipped panes without `options`/`preview` in the past; a
    // missing array must not take the whole route down.
    vi.mocked(api.getTmuxLayout).mockResolvedValue({
      available: true,
      reason: null,
      panes: [{ ...pane(), options: undefined, preview: undefined, capabilities: undefined }],
    } as never);
    render(
      <MemoryRouter initialEntries={["/panes"]}>
        <PanesPage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(api.getTmuxLayout).toHaveBeenCalled());
  });
});
