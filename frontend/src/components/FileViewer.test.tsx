/** FileViewer: the global click-to-view modal. Markdown paths render via the Markdown
 * component (Raw toggle switches to source); other paths render a code block; a failed
 * read surfaces the error. api.readFile is mocked. */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import FileViewer from "./FileViewer";
import { openFile } from "../util/openFile";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { readFile: vi.fn() } }));

const mockRead = api.readFile as unknown as ReturnType<typeof vi.fn>;
const page = (content: string, path = "/p/f", next: number | null = null) => ({
  path,
  size: content.length,
  offset: 0,
  content,
  next_offset: next,
});

describe("FileViewer", () => {
  beforeEach(() => mockRead.mockReset());

  it("renders markdown as rich HTML for a .md file", async () => {
    mockRead.mockResolvedValue(page("# Hello\n\nsome text", "/p/plan.md"));
    render(<FileViewer />);
    openFile("/home/luke/.claude/plans/plan.md");
    expect(await screen.findByRole("heading", { name: "Hello" })).toBeTruthy();
  });

  it("renders code (no heading) for a non-markdown file", async () => {
    const { container } = render(<FileViewer />);
    mockRead.mockResolvedValue(page("const x = 1", "/p/a.ts"));
    openFile("/p/a.ts");
    expect(await screen.findByText(/const x = 1/)).toBeTruthy();
    expect(screen.queryByRole("heading")).toBeNull();
    expect(container.querySelector(".codeblock")).toBeTruthy();
  });

  it("surfaces the error message when the read fails", async () => {
    mockRead.mockRejectedValue(new Error("path is outside the project dirs"));
    render(<FileViewer />);
    await act(async () => {
      openFile("/etc/passwd");
    });
    expect(screen.getByText(/outside the project dirs/)).toBeTruthy();
    // vitest's spy re-invokes the mock once during teardown to record its settled result;
    // neutralize it so that echo doesn't produce a stray (already-handled) rejection that
    // gets misreported as unhandled.
    mockRead.mockResolvedValue(page(""));
  });

  it("Raw toggle switches rendered markdown to source", async () => {
    const { container } = render(<FileViewer />);
    mockRead.mockResolvedValue(page("# Hello", "/p/plan.md"));
    openFile("/p/plan.md");
    await screen.findByRole("heading", { name: "Hello" });
    fireEvent.click(screen.getByTitle("View source"));
    expect(screen.queryByRole("heading", { name: "Hello" })).toBeNull();
    expect(container.querySelector(".codeblock")).toBeTruthy();
  });

  it("closes on the scrim click", async () => {
    const { container } = render(<FileViewer />);
    mockRead.mockResolvedValue(page("# Hi", "/p/plan.md"));
    openFile("/p/plan.md");
    await screen.findByRole("heading", { name: "Hi" });
    fireEvent.click(container.querySelector(".file-viewer-scrim")!);
    expect(container.querySelector(".file-viewer")).toBeNull();
  });
});
