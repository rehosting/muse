/** The "/" autocomplete hook: opens on a leading slash-token, filters, and
 * replaces the composer text on pick. Commands load lazily and only once. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSlashMenu } from "./SlashMenu";

vi.mock("../api/client", () => ({ api: { getPaneCommands: vi.fn() } }));
import { api } from "../api/client";

const getPaneCommands = vi.mocked(api.getPaneCommands);
afterEach(() => getPaneCommands.mockReset());

const CMDS = [
  { name: "compact", description: "compact it", source: "builtin" as const },
  { name: "clear", description: "clear it", source: "builtin" as const },
  { name: "deploy", description: "ship", source: "project" as const },
];

function Harness({ paneId = "%1", enabled = true }: { paneId?: string; enabled?: boolean }) {
  const [text, setText] = useState("");
  const slash = useSlashMenu({ paneId, text, setText, enabled });
  return (
    <div>
      <textarea
        data-testid="input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (slash.onKeyDown(e)) e.preventDefault();
        }}
      />
      {slash.menu}
    </div>
  );
}

describe("useSlashMenu", () => {
  it("stays closed until a leading slash is typed", () => {
    getPaneCommands.mockResolvedValue(CMDS);
    render(<Harness />);
    expect(getPaneCommands).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("opens and filters on the slash query", async () => {
    getPaneCommands.mockResolvedValue(CMDS);
    render(<Harness />);
    fireEvent.change(screen.getByTestId("input"), { target: { value: "/cl" } });
    await waitFor(() => expect(getPaneCommands).toHaveBeenCalledWith("%1"));
    expect(await screen.findByText("/clear")).toBeTruthy();
    expect(screen.queryByText("/deploy")).toBeNull();
  });

  it("picking a row replaces the text with the command", async () => {
    getPaneCommands.mockResolvedValue(CMDS);
    render(<Harness />);
    const input = screen.getByTestId("input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "/dep" } });
    const row = (await screen.findByText("/deploy")).closest(".slash-menu-row")!;
    // A still tap (down then up, no move) picks; a drag would not.
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10 });
    fireEvent.pointerUp(row, { clientX: 10, clientY: 10 });
    await waitFor(() => expect(input.value).toBe("/deploy "));
    // Trailing space means the query no longer matches — menu is gone.
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("a drag scrolls instead of selecting", async () => {
    getPaneCommands.mockResolvedValue(CMDS);
    render(<Harness />);
    const input = screen.getByTestId("input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "/dep" } });
    const row = (await screen.findByText("/deploy")).closest(".slash-menu-row")!;
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10 });
    fireEvent.pointerMove(row, { clientX: 10, clientY: 60 }); // dragged 50px
    fireEvent.pointerUp(row, { clientX: 10, clientY: 60 });
    expect(input.value).toBe("/dep"); // unchanged — no pick
  });

  it("closes once a space (arguments) is typed", async () => {
    getPaneCommands.mockResolvedValue(CMDS);
    render(<Harness />);
    fireEvent.change(screen.getByTestId("input"), { target: { value: "/compact" } });
    expect(await screen.findByRole("listbox")).toBeTruthy();
    fireEvent.change(screen.getByTestId("input"), { target: { value: "/compact now" } });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("does nothing when disabled", () => {
    getPaneCommands.mockResolvedValue(CMDS);
    render(<Harness enabled={false} />);
    fireEvent.change(screen.getByTestId("input"), { target: { value: "/cl" } });
    expect(getPaneCommands).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
