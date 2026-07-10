/** Plans and questions render inline in the conversation (PlanLine/QuestionLine)
 * instead of as a collapsed tool row — so a plan is reviewable in the reader and
 * a pending question is visible there, without switching to the raw terminal. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConversationView from "./ConversationView";
import { makeToolOnlyItem } from "../util/readerFixtures";
import type { ThreadItem } from "../api/types";

// Markdown is exercised elsewhere; here render its text so we can assert content.
vi.mock("./Markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

const noop = () => {};

function renderView(items: ThreadItem[]) {
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
      focus
    />,
  );
}

function planItem(plan: string): ThreadItem {
  const it = makeToolOnlyItem("plan", 1, 0, { name: "ExitPlanMode" });
  it.blocks[0].tool_use!.input = { plan };
  return it;
}

function questionItem(): ThreadItem {
  const it = makeToolOnlyItem("q", 1, 0, { name: "AskUserQuestion" });
  it.blocks[0].tool_use!.input = {
    questions: [
      {
        question: "Which database?",
        options: [
          { label: "Postgres", description: "relational" },
          { label: "SQLite", description: "embedded" },
        ],
      },
    ],
  };
  return it;
}

describe("reader inline plan / question", () => {
  it("renders the plan body inline, expanded by default in the reader", () => {
    renderView([planItem("## Plan\n\nFirst step, then second step.")]);
    expect(screen.getByText("Plan")).toBeTruthy();
    expect(screen.getByText(/First step, then second step/)).toBeTruthy();
    // Focus (reader) mode opens the plan so it's readable without a tap; the
    // toggle then offers to collapse it.
    const toggle = screen.getByText("▴ collapse plan");
    fireEvent.click(toggle);
    expect(screen.getByText("▾ read full plan")).toBeTruthy();
  });

  it("does not collapse the plan into a '… steps' tool run", () => {
    renderView([planItem("Do the thing.")]);
    expect(screen.queryByText(/steps/)).toBeNull();
    expect(screen.queryByText("ExitPlanMode")).toBeNull(); // rendered as "Plan", not the raw name
  });

  it("renders a question with its options inline", () => {
    renderView([questionItem()]);
    expect(screen.getByText("Question")).toBeTruthy();
    expect(screen.getByText("Which database?")).toBeTruthy();
    expect(screen.getByText("Postgres")).toBeTruthy();
    expect(screen.getByText("SQLite")).toBeTruthy();
  });
});
