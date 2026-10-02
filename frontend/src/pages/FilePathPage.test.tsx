/** Deep-linked file reader: pasting an absolute path onto the host reads the file
 * instead of hitting the router's error page. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({ api: { readFile: vi.fn() } }));
import { api } from "../api/client";
import FilePathPage from "./FilePathPage";

const mockRead = api.readFile as unknown as ReturnType<typeof vi.fn>;
const page = (content: string, next: number | null = null) => ({
  path: "/p/f", size: content.length, offset: 0, content, next_offset: next,
});

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <FilePathPage />
    </MemoryRouter>,
  );

describe("FilePathPage", () => {
  beforeEach(() => mockRead.mockReset());

  it("reads the URL path as a file and renders markdown", async () => {
    mockRead.mockResolvedValue(page("# Plan\n\nstep one"));
    at("/home/luke/workspace/kernel-lift-c/work/plan.md");
    await waitFor(() =>
      expect(mockRead).toHaveBeenCalledWith("/home/luke/workspace/kernel-lift-c/work/plan.md"),
    );
    expect(await screen.findByRole("heading", { name: "Plan" })).toBeTruthy();
  });

  it("decodes a percent-encoded path before asking the server", async () => {
    mockRead.mockResolvedValue(page("x"));
    at("/home/luke/my%20notes/plan.md");
    await waitFor(() => expect(mockRead).toHaveBeenCalledWith("/home/luke/my notes/plan.md"));
  });

  it("shows a not-found message for a mistyped app route, without calling the API", () => {
    at("/panez");
    expect(screen.getByText("Not found")).toBeTruthy();
    expect(mockRead).not.toHaveBeenCalled();
  });

  it("explains the server's path policy when a read is refused", async () => {
    mockRead.mockRejectedValue(new Error("path outside indexed dirs"));
    at("/etc/shadow.conf");
    await waitFor(() => expect(screen.getByText(/outside indexed dirs/)).toBeTruthy());
    expect(screen.getByText(/only reads files under/)).toBeTruthy();
    // Same teardown quirk FileViewer.test.tsx documents: vitest re-invokes the spy when
    // recording its settled result, and that echo would be reported as an unhandled
    // rejection. Leave the mock resolving.
    mockRead.mockResolvedValue(page(""));
  });

  it("scrolls: the root is the app's scroll container", async () => {
    // body is overflow:hidden by design — a page that doesn't sit inside .list-wrap
    // is silently clipped and can't be scrolled. Cheap invariant, real bug caught.
    mockRead.mockResolvedValue(page("# Long\n\n" + "para\n\n".repeat(400)));
    const { container } = at("/home/luke/long.md");
    await waitFor(() => expect(screen.getByRole("heading", { name: "Long" })).toBeTruthy());
    expect(container.querySelector(".list-wrap.filepath-page")).toBeTruthy();
  });

  it("uses the scroll container for the not-found view too", () => {
    const { container } = at("/panez");
    expect(container.querySelector(".list-wrap.filepath-page")).toBeTruthy();
  });

  it("offers to page through a large file", async () => {
    mockRead.mockResolvedValue(page("chunk one ", 40000));
    at("/home/luke/big.md");
    await waitFor(() => expect(screen.getByText(/Load more/)).toBeTruthy());
  });
});

describe("FilePathPage wrap mode", () => {
  beforeEach(() => {
    mockRead.mockReset();
    localStorage.removeItem("fileWrap");
  });

  it("toggles wrapping and shares the preference with the modal viewer", async () => {
    mockRead.mockResolvedValue(page("x".repeat(400)));
    const { container } = at("/home/luke/app.log");
    await waitFor(() => expect(mockRead).toHaveBeenCalled());
    const root = container.firstElementChild!;
    expect(root.classList.contains("wrap")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Wrap" }));
    expect(root.classList.contains("wrap")).toBe(true);
    // same key the modal reads, so the two viewers can't disagree
    expect(localStorage.getItem("fileWrap")).toBe("1");
  });

  it("honours a preference set elsewhere", async () => {
    localStorage.setItem("fileWrap", "1");
    mockRead.mockResolvedValue(page("hello"));
    const { container } = at("/home/luke/notes.md");
    await waitFor(() => expect(mockRead).toHaveBeenCalled());
    expect(container.firstElementChild!.classList.contains("wrap")).toBe(true);
  });
});
