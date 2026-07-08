/** Reader-mode rendering contract: non-virtualized ConversationView must render
 * the ENTIRE window it's given — first and last item — so the panes reader can
 * scroll to the newest message. Guards against internal render-windowing (the
 * virtualize path) silently applying and eating the tail. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConversationView from "./ConversationView";
import { makeItems } from "../util/readerFixtures";

// Markdown pulls react-markdown + highlight.js; a plain passthrough keeps this
// test about ConversationView's own windowing, not markdown rendering.
vi.mock("./Markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

const noop = () => {};

function renderView(count: number) {
  return render(
    <ConversationView
      items={makeItems(0, count)}
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
}

describe("ConversationView in reader mode (compact+focus, non-virtualized)", () => {
  it("renders both the first and the LAST item of a tail window", () => {
    renderView(40);
    expect(screen.getByText("text of m0")).toBeTruthy();
    expect(screen.getByText("text of m39")).toBeTruthy();
  });

  it("renders every item of an accumulated multi-window thread", () => {
    renderView(150);
    // 150 > any window/threshold constant in play — nothing may be dropped.
    expect(screen.getByText("text of m0")).toBeTruthy();
    expect(screen.getByText("text of m75")).toBeTruthy();
    expect(screen.getByText("text of m149")).toBeTruthy();
  });
});
