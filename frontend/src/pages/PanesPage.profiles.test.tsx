/** NewWindowLauncher: the split "New window" control. Primary button launches the
 * built-in "Claude" profile; the ▾ caret opens the profile menu (from api.listProfiles).
 * A params-less profile launches immediately; a params profile opens a form first. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { listProfiles: vi.fn(), launchProfile: vi.fn() },
}));
import { api } from "../api/client";
import { NewWindowLauncher } from "./PanesPage";
import type { Profile } from "../api/types";

function profile(over: Partial<Profile>): Profile {
  return { name: "p", cwd: "~", command: "claude", params: [], builtin: false, ...over };
}

beforeEach(() => {
  vi.mocked(api.listProfiles).mockReset();
  vi.mocked(api.launchProfile).mockReset();
});

describe("NewWindowLauncher", () => {
  it("launches the built-in Claude profile from the primary button", () => {
    const onLaunch = vi.fn();
    render(<NewWindowLauncher group={null} busy={false} onLaunch={onLaunch} />);
    fireEvent.click(screen.getByText("New window"));
    expect(onLaunch).toHaveBeenCalledWith("Claude", {});
    expect(api.listProfiles).not.toHaveBeenCalled(); // no menu fetch for the fast path
  });

  it("opens the caret menu, lists profiles, and launches a params-less one immediately", async () => {
    vi.mocked(api.listProfiles).mockResolvedValue([
      profile({ name: "Claude", builtin: true }),
      profile({ name: "web", command: "npm run dev & claude" }),
    ]);
    const onLaunch = vi.fn();
    render(<NewWindowLauncher group={null} busy={false} onLaunch={onLaunch} />);
    fireEvent.click(screen.getByTitle("Launch a profile"));
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
