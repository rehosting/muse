/** Groups = tmux sessions: buildGroups aggregates windows per session; a task
 * row's "⋯" menu offers the OTHER groups + "New group…" as move targets. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { buildGroups, buildWindows, DeckGroupSwitcher, MoveMenu, TaskRow } from "./PanesPage";
import type { TmuxPane } from "../api/types";

function pane(over: Partial<TmuxPane>): TmuxPane {
  return {
    provider: "claude",
    pane_id: "%1",
    session_name: "a",
    window_index: 0,
    window_id: "@1",
    window_name: "w",
    window_active: false,
    pane_index: 0,
    pane_active: false,
    command: "claude",
    cwd: "/x",
    title: "",
    session_attached: true,
    last_activity: 0,
    muse_session_id: "s",
    context_pct: null,
    queued: 0,
    status: "idle",
    attention: "",
    mode: null,
    capabilities: {
      mode_switch: true,
      session_reply: true,
      rich_reply: true,
      queue_replies: true,
      reader: true,
      drive: true,
      slash_commands: true,
    },
    preview: "",
    preview_tail: "",
    options: [],
    ...over,
  };
}

describe("buildGroups", () => {
  it("counts windows per session and escalates to the most-urgent status", () => {
    const panes = [
      pane({ pane_id: "%1", session_name: "work", window_index: 0, status: "idle" }),
      pane({ pane_id: "%2", session_name: "work", window_index: 1, status: "needs_you" }),
      pane({ pane_id: "%3", session_name: "exp", window_index: 0, status: "working" }),
    ];
    const groups = buildGroups(buildWindows(panes));
    const work = groups.find((g) => g.name === "work")!;
    const exp = groups.find((g) => g.name === "exp")!;
    expect(work.windows).toBe(2);
    expect(work.status).toBe("needs_you"); // most urgent across its windows
    expect(exp.windows).toBe(1);
    // Attention-first ordering → needs_you group sorts ahead of the working one.
    expect(groups[0].name).toBe("work");
  });

  it("marks a session of only idle non-Claude shells as a deletable placeholder", () => {
    const groups = buildGroups(
      buildWindows([
        pane({
          provider: null,
          session_name: "empty",
          command: "bash",
          muse_session_id: null,
          status: "idle",
          capabilities: {
            mode_switch: false,
            session_reply: false,
            rich_reply: false,
            queue_replies: false,
            reader: false,
            drive: false,
            slash_commands: false,
          },
        }),
        pane({ pane_id: "%9", session_name: "live", command: "claude", status: "working" }),
      ]),
    );
    expect(groups.find((g) => g.name === "empty")!.placeholder).toBe(true);
    expect(groups.find((g) => g.name === "live")!.placeholder).toBe(false);
  });
});

describe("TaskRow move menu", () => {
  const win = buildWindows([pane({ session_name: "a", window_id: "@7" })])[0];

  it("offers the other groups + New group…, and reports the picked target", () => {
    const onMove = vi.fn();
    const onMoveNew = vi.fn();
    render(
      <TaskRow
        win={win}
        onOpen={() => {}}
        desktop
        groups={["a", "b", "c"]}
        onMove={onMove}
        onMoveNew={onMoveNew}
      />,
    );
    fireEvent.click(screen.getByTitle("Move to a group"));
    // The row's own session ("a") is not a target; the others + New group are.
    expect(screen.queryByRole("menuitem")).toBeNull(); // rows are buttons, not menuitems
    expect(screen.getByText("b")).toBeTruthy();
    expect(screen.getByText("c")).toBeTruthy();
    expect(screen.queryByText("a")).toBeNull();
    fireEvent.click(screen.getByText("b"));
    expect(onMove).toHaveBeenCalledWith("b");
    expect(onMoveNew).not.toHaveBeenCalled();
  });

  it("routes New group… to the create-then-move handler", () => {
    const onMoveNew = vi.fn();
    render(
      <TaskRow win={win} onOpen={() => {}} desktop groups={["a"]} onMove={() => {}} onMoveNew={onMoveNew} />,
    );
    fireEvent.click(screen.getByTitle("Move to a group"));
    fireEvent.click(screen.getByText("New group…"));
    expect(onMoveNew).toHaveBeenCalledTimes(1);
  });
});

describe("TaskRow rename (desktop ✎)", () => {
  const win = buildWindows([pane({ session_name: "a", window_id: "@7", window_name: "old-name" })])[0];

  it("shows the ✎ on desktop and reports a rename request", () => {
    const onRenameWindow = vi.fn();
    render(<TaskRow win={win} onOpen={() => {}} desktop onRenameWindow={onRenameWindow} />);
    fireEvent.click(screen.getByTitle("Rename window"));
    expect(onRenameWindow).toHaveBeenCalledTimes(1);
  });

  it("hides the ✎ on touch (mobile uses hold instead)", () => {
    render(<TaskRow win={win} onOpen={() => {}} desktop={false} onRenameWindow={() => {}} />);
    expect(screen.queryByTitle("Rename window")).toBeNull();
  });
});

describe("DeckGroupSwitcher (deck section switcher)", () => {
  it("opens a dropdown of sections and switches to the picked one", () => {
    const onSwitch = vi.fn();
    render(<DeckGroupSwitcher group="work" groups={["work", "exp", "ops"]} onSwitch={onSwitch} />);
    fireEvent.click(screen.getByTitle("Switch section"));
    expect(screen.getByText("All sections")).toBeTruthy();
    fireEvent.click(screen.getByText("exp"));
    expect(onSwitch).toHaveBeenCalledWith("exp");
  });

  it("has an ✕ that widens back to All when a section is active", () => {
    const onSwitch = vi.fn();
    render(<DeckGroupSwitcher group="work" groups={["work", "exp"]} onSwitch={onSwitch} />);
    fireEvent.click(screen.getByTitle("Show all sections"));
    expect(onSwitch).toHaveBeenCalledWith(null);
  });

  it("shows no ✕ when already on All", () => {
    render(<DeckGroupSwitcher group={null} groups={["work"]} onSwitch={() => {}} />);
    expect(screen.queryByTitle("Show all sections")).toBeNull();
  });
});

describe("MoveMenu (deck header variant)", () => {
  it("renders the Move ▾ trigger, excludes the current group, and reports the pick", () => {
    const onMove = vi.fn();
    const onMoveNew = vi.fn();
    render(
      <MoveMenu
        groups={["work", "exp", "ops"]}
        currentSession="work"
        onMove={onMove}
        onMoveNew={onMoveNew}
        triggerClass="pane-card-move"
        triggerLabel="Move ▾"
        title="Move this window to a group"
      />,
    );
    fireEvent.click(screen.getByTitle("Move this window to a group"));
    expect(screen.queryByText("work")).toBeNull(); // current session isn't a target
    expect(screen.getByText("exp")).toBeTruthy();
    expect(screen.getByText("ops")).toBeTruthy();
    fireEvent.click(screen.getByText("ops"));
    expect(onMove).toHaveBeenCalledWith("ops");
    // Menu closed after picking.
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
