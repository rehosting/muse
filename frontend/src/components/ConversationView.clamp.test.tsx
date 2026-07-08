/** Focus (phone reader) mode clamps a verbose tool result to FOCUS_RESULT_LINES
 * — but never more than the tool's own format limit — with a tap to expand. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConversationView from "./ConversationView";
import type { ContentBlock, ThreadItem } from "../api/types";

vi.mock("./Markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

const noop = () => {};

// An assistant item with one Bash call whose result is 6 lines of output.
function bashItem(uuid: string, lines: number): ThreadItem {
  const content = Array.from({ length: lines }, (_, i) => `line-${i}`).join("\n");
  const block: ContentBlock = {
    kind: "tool_use" as ContentBlock["kind"],
    text: null,
    tool_use: {
      id: `${uuid}-t`,
      name: "Bash",
      input: { command: "ls" },
      caller: null,
      result: { content, is_error: false } as unknown as NonNullable<
        NonNullable<ContentBlock["tool_use"]>["result"]
      >,
      subagent: null,
    },
  };
  return {
    uuid,
    parent_uuid: null,
    role: "assistant",
    type: "assistant",
    timestamp: null,
    blocks: [block],
    text: "",
    usage: null,
    model: null,
    is_sidechain: false,
    level: null,
  };
}

function renderView(focus: boolean) {
  return render(
    <ConversationView
      items={[bashItem("b0", 6)]}
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

describe("focus-mode tool-result clamp", () => {
  it("shows at most 3 result lines collapsed, with a +N indicator", () => {
    renderView(true);
    expect(screen.getByText("line-0")).toBeTruthy();
    expect(screen.getByText("line-2")).toBeTruthy();
    expect(screen.queryByText("line-3")).toBeNull();
    expect(screen.getByText(/\+3 lines/)).toBeTruthy();
  });

  it("collapsed rows are single-line truncated (clamped class); expand clears it", () => {
    renderView(true);
    expect(document.querySelector(".cc-result-clamped")).toBeTruthy();
    fireEvent.click(screen.getByText("line-0").closest(".cc-tool-line")!);
    expect(document.querySelector(".cc-result-clamped")).toBeNull();
  });

  it("tapping the tool line expands to the full output and un-clamps the header", () => {
    renderView(true);
    expect(document.querySelector(".cc-tool-head.open")).toBeNull();
    fireEvent.click(screen.getByText("line-0").closest(".cc-tool-line")!);
    expect(screen.getByText("line-5")).toBeTruthy();
    expect(document.querySelector(".cc-tool-head.open")).toBeTruthy();
  });
});
