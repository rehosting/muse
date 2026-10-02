/** Focus-mode tool-run grouping: a stretch of tool-only items must fold into a
 * single tappable row (with the live current step still visible), expand in
 * place, survive live appends without collapsing, and leave the non-focus
 * render completely untouched. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConversationView from "./ConversationView";
import {
  makeHiddenUserItem,
  makeItem,
  makeToolOnlyItem,
} from "../util/readerFixtures";
import type { ThreadItem } from "../api/types";

vi.mock("./Markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

const noop = () => {};

function renderView(items: ThreadItem[], focus = true) {
  return render(
    <ConversationView
      items={items}
      cwd="/proj"
      model={null}
      selectedToolId={null}
      onSelectTool={noop}
      registerToolRef={noop}
      bookmarks={{}}
      onSaveBookmark={noop}
      onRemoveBookmark={noop}
      compact
      focus={focus}
    />,
  );
}

// text — 4 completed tool calls (with carriers) — text
function conversation(): ThreadItem[] {
  const items: ThreadItem[] = [makeItem("intro")];
  for (let i = 0; i < 4; i++) {
    items.push(makeToolOnlyItem(`t${i}`, 1, 1, { name: "Bash" }));
    items.push(makeHiddenUserItem(`t${i}-r`));
  }
  items.push(makeItem("outro"));
  return items;
}

describe("ConversationView tool-run grouping (focus mode)", () => {
  it("collapses a run to one row and hides the member tool lines", () => {
    renderView(conversation());
    expect(screen.getByText(/4 steps/)).toBeTruthy();
    expect(screen.getByText(/Bash ×4/)).toBeTruthy();
    // The individual tool lines are folded away…
    expect(screen.queryAllByText("Bash")).toHaveLength(0);
    // …while the surrounding conversation still renders.
    expect(screen.getByText("text of intro")).toBeTruthy();
    expect(screen.getByText("text of outro")).toBeTruthy();
  });

  it("tap expands the run in place; the header folds it again", () => {
    renderView(conversation());
    fireEvent.click(screen.getByText(/4 steps/));
    expect(screen.getAllByText("Bash")).toHaveLength(4);
    expect(screen.getByText(/tap to fold/)).toBeTruthy();
    fireEvent.click(screen.getByText(/4 steps/));
    expect(screen.queryAllByText("Bash")).toHaveLength(0);
  });

  it("stays open across a live append (key is the stable first member)", () => {
    const items = conversation().slice(0, -1); // drop outro: run is the tail
    const { rerender } = renderView(items);
    fireEvent.click(screen.getByText(/4 steps/));
    expect(screen.getAllByText("Bash")).toHaveLength(4);
    rerender(
      <ConversationView
        items={[...items, makeToolOnlyItem("t9", 1, 0, { name: "Bash" })]}
        cwd="/proj"
        model={null}
        selectedToolId={null}
        onSelectTool={noop}
        registerToolRef={noop}
        bookmarks={{}}
        onSaveBookmark={noop}
        onRemoveBookmark={noop}
        compact
        focus
      />,
    );
    // Still open — now with the appended call rendered too.
    expect(screen.getAllByText("Bash")).toHaveLength(5);
  });

  it("a collapsed run at the live tail shows its current step", () => {
    const items = conversation().slice(0, -1);
    items.push(makeToolOnlyItem("live", 1, 0, { name: "Grep" })); // running
    renderView(items);
    expect(screen.getByText(/5 steps/)).toBeTruthy();
    // The newest call is visible with its running connector…
    expect(screen.getByText("Grep")).toBeTruthy();
    expect(screen.getByText("running…")).toBeTruthy();
    // …but the finished members stay folded.
    expect(screen.queryAllByText("Bash")).toHaveLength(0);
  });

  it("non-focus render is unchanged: no group rows, every tool line present", () => {
    renderView(conversation(), false);
    expect(screen.queryByText(/4 steps/)).toBeNull();
    expect(screen.getAllByText("Bash")).toHaveLength(4);
  });
});
