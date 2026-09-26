/** Focus mode (the panes reader): a file tool's summary arg — the file_path shown
 * for Read/Edit/Write — is a click target that opens the global file viewer, without
 * also toggling the tool line open. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConversationView from "./ConversationView";
import { makeItem } from "../util/readerFixtures";
import type { ContentBlock, ThreadItem } from "../api/types";

vi.mock("./Markdown", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));
vi.mock("../util/openFile", () => ({ openFile: vi.fn(), VIEW_FILE_EVENT: "muse:view-file" }));
import { openFile } from "../util/openFile";
const openFileMock = vi.mocked(openFile);

function fileToolItem(uuid: string, name: string, path: string): ThreadItem {
  const block: ContentBlock = {
    kind: "tool_use" as ContentBlock["kind"],
    text: null,
    tool_use: {
      id: `${uuid}-t0`,
      name,
      input: { file_path: path, content: "x" },
      caller: null,
      result: { content: "ok", is_error: false, truncated: false } as unknown as NonNullable<
        NonNullable<ContentBlock["tool_use"]>["result"]
      >,
      subagent: null,
    },
  };
  return { ...makeItem(uuid), text: null, blocks: [block] };
}

function bashItem(uuid: string): ThreadItem {
  const block: ContentBlock = {
    kind: "tool_use" as ContentBlock["kind"],
    text: null,
    tool_use: {
      id: `${uuid}-t0`,
      name: "Bash",
      input: { command: "echo hi" },
      caller: null,
      result: { content: "hi", is_error: false, truncated: false } as unknown as NonNullable<
        NonNullable<ContentBlock["tool_use"]>["result"]
      >,
      subagent: null,
    },
  };
  return { ...makeItem(uuid), text: null, blocks: [block] };
}

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

describe("ConversationView file-path links (panes reader)", () => {
  it("opens the viewer when a Write's path is clicked, without toggling the line", () => {
    openFileMock.mockClear();
    renderView([
      makeItem("a"),
      fileToolItem("w", "Write", "/proj/src/main.ts"),
      makeItem("b"),
    ]);
    const link = screen.getByTitle("View /proj/src/main.ts");
    // It's a button (not the plain summary span) and doesn't expand the line's result.
    expect(link.tagName).toBe("BUTTON");
    fireEvent.click(link);
    expect(openFileMock).toHaveBeenCalledWith("/proj/src/main.ts");
    expect(screen.queryByText(/tap to fold/)).toBeNull();
  });

  it("also wires Read paths, but leaves non-file tools (Bash) as plain args", () => {
    renderView([
      makeItem("a"),
      fileToolItem("r", "Read", "/proj/x.md"),
      bashItem("s"),
      makeItem("b"),
    ]);
    // Read path is a click target…
    expect(screen.getByTitle("View /proj/x.md").tagName).toBe("BUTTON");
    // …while a Bash command arg stays a plain span (no view button for it).
    expect(screen.getByText("(echo hi)").tagName).toBe("SPAN");
    expect(screen.queryByTitle("View echo hi")).toBeNull();
  });
});
