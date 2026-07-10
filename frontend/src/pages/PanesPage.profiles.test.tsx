/** NewWindowLauncher: the split "New window" control. Primary button launches the
 * built-in "Claude" profile; the ▾ caret opens the profile menu (from api.listProfiles).
 * A params-less profile launches immediately; a params profile opens a form first. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { listProfiles: vi.fn(), launchProfile: vi.fn() },
}));
import { api } from "../api/client";
import { NewWindowLauncher, waitForPane } from "./PanesPage";
import type { Profile, TmuxLayout } from "../api/types";

function profile(over: Partial<Profile>): Profile {
  return {
    name: "p",
    provider: "claude",
    cwd: "~",
    command: "claude",
    params: [],
    builtin: false,
    ...over,
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.listProfiles).mockReset();
  vi.mocked(api.launchProfile).mockReset();
});

describe("NewWindowLauncher", () => {
  it("waits briefly for a launched pane to appear in tmux layout", async () => {
    const layouts: TmuxLayout[] = [
      { available: true, panes: [], reason: null },
      {
        available: true,
        panes: [
          {
            provider: "codex",
            pane_id: "%9",
            session_name: "main",
            window_index: 0,
            window_id: "@9",
            window_name: "Codex",
            window_active: true,
            pane_index: 0,
            pane_active: true,
            command: "node",
            cwd: "/tmp",
            title: "",
            session_attached: true,
            last_activity: 0,
            muse_session_id: null,
            context_pct: null,
            queued: 0,
            status: "idle",
            attention: "",
            mode: null,
            capabilities: {
              mode_switch: false,
              session_reply: false,
              rich_reply: false,
              queue_replies: false,
              reader: false,
              drive: false,
              slash_commands: false,
            },
            preview: "",
            preview_tail: "",
            options: [],
          },
        ],
        reason: null,
      },
    ];
    let calls = 0;
    const fresh = await waitForPane(async () => layouts[calls++] ?? layouts[layouts.length - 1], "%9", 3, 0);
    expect(calls).toBe(2);
    expect(fresh.panes[0].pane_id).toBe("%9");
  });

  it("launches the default built-in provider from the primary button", () => {
    const onLaunch = vi.fn();
    render(<NewWindowLauncher group={null} busy={false} onLaunch={onLaunch} />);
    fireEvent.click(screen.getByText("New Claude"));
    expect(onLaunch).toHaveBeenCalledWith("Claude", {});
    expect(api.listProfiles).not.toHaveBeenCalled(); // no menu fetch for the fast path
  });

  it("opens the caret menu, lists profiles, and launches a params-less one immediately", async () => {
    vi.mocked(api.listProfiles).mockResolvedValue([
      profile({ name: "Claude", builtin: true }),
      profile({ name: "Gemini", provider: "gemini", command: "antigravity", builtin: true }),
      profile({ name: "web", command: "npm run dev & claude" }),
    ]);
    const onLaunch = vi.fn();
    render(<NewWindowLauncher group={null} busy={false} onLaunch={onLaunch} />);
    fireEvent.click(screen.getByTitle("Launch a profile"));
    expect(await screen.findByText("Providers")).toBeTruthy();
    expect(screen.getByText("Profiles")).toBeTruthy();
    fireEvent.click(await screen.findByText("web"));
    expect(onLaunch).toHaveBeenCalledWith("web", {});
  });

  it("prompts for a profile's params, then launches with the collected values + group", async () => {
    vi.mocked(api.listProfiles).mockResolvedValue([
      profile({
        name: "igloo dev",
        command: "./dev.sh {issue} && claude",
        params: [{ key: "issue", prompt: "Issue name", default: "" }],
      }),
    ]);
    const onLaunch = vi.fn();
    render(<NewWindowLauncher group="work" busy={false} onLaunch={onLaunch} />);
    fireEvent.click(screen.getByTitle("Launch a profile"));
    fireEvent.click(await screen.findByText("igloo dev"));
    // Picking a params profile opens the form, not an immediate launch.
    expect(onLaunch).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "PROJ-12" } });
    fireEvent.click(screen.getByText(/^Launch/));
    expect(onLaunch).toHaveBeenCalledWith("igloo dev", { issue: "PROJ-12" });
  });

  it("shows a config error in the menu when the profiles file is broken", async () => {
    vi.mocked(api.listProfiles).mockRejectedValue(new Error("profiles.toml: bad table"));
    render(<NewWindowLauncher group={null} busy={false} onLaunch={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Launch a profile"));
    await waitFor(() => expect(screen.getByText(/profiles.toml: bad table/)).toBeTruthy());
  });
});
