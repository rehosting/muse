/** Upload page: the deliverable is the PATH, so these tests are mostly about the path
 * landing in front of the user (and in the clipboard) rather than about the transfer. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: { listUploads: vi.fn(), uploadFiles: vi.fn(), deleteUpload: vi.fn() },
}));
const copyToClipboard = vi.fn(async (_text: string) => true);
vi.mock("../util/shell", () => ({ copyToClipboard: (t: string) => copyToClipboard(t) }));
const openFile = vi.fn((_path: string) => {});
vi.mock("../util/openFile", () => ({ openFile: (p: string) => openFile(p) }));

import { api } from "../api/client";
import UploadsPage from "./UploadsPage";

const listing = (files: unknown[] = [], over = {}) =>
  ({ root: "/tmp/muse-uploads-1002", ttl_hours: 48, max_mb: 64, files, errors: [], ...over }) as never;

const shot = {
  name: "shot.png",
  path: "/tmp/muse-uploads-1002/shot.png",
  size: 24_000,
  mtime: new Date().toISOString(),
};

const pick = (files: File[]) => {
  const input = screen.getByLabelText("Choose files to upload") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: files, configurable: true });
  fireEvent.change(input);
};

beforeEach(() => {
  vi.clearAllMocks();
  copyToClipboard.mockResolvedValue(true);
});

describe("UploadsPage", () => {
  it("shows the drop dir and the limits up front, not on rejection", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing());
    render(<UploadsPage />);
    await waitFor(() => expect(screen.getByText("/tmp/muse-uploads-1002")).toBeTruthy());
    expect(screen.getByText(/up to 64 MB each/)).toBeTruthy();
    expect(screen.getByText(/swept after 48h/)).toBeTruthy();
  });

  it("uploads a picked file and surfaces its absolute path", async () => {
    vi.mocked(api.listUploads).mockResolvedValueOnce(listing()).mockResolvedValue(listing([shot]));
    vi.mocked(api.uploadFiles).mockResolvedValue(listing([shot]));
    render(<UploadsPage />);
    await waitFor(() => expect(api.listUploads).toHaveBeenCalled());

    const file = new File(["x"], "shot.png", { type: "image/png" });
    pick([file]);

    await waitFor(() => expect(api.uploadFiles).toHaveBeenCalledWith([file]));
    expect(await screen.findByText("/tmp/muse-uploads-1002/shot.png")).toBeTruthy();
  });

  it("copies the path of a fresh upload without being asked", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing([shot]));
    vi.mocked(api.uploadFiles).mockResolvedValue(listing([shot]));
    render(<UploadsPage />);
    await waitFor(() => expect(api.listUploads).toHaveBeenCalled());
    pick([new File(["x"], "shot.png")]);
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(shot.path));
  });

  it("copies a path on tap and says so", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing([shot]));
    render(<UploadsPage />);
    fireEvent.click(await screen.findByTitle("Copy this path"));
    await waitFor(() => expect(screen.getByText("copied")).toBeTruthy());
    expect(copyToClipboard).toHaveBeenCalledWith(shot.path);
  });

  it("falls back to a readable message when the clipboard is unavailable", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing([shot]));
    copyToClipboard.mockResolvedValue(false);
    render(<UploadsPage />);
    fireEvent.click(await screen.findByTitle("Copy this path"));
    await waitFor(() => expect(screen.getByText(/long-press the path/)).toBeTruthy());
  });

  it("keeps the paths that landed when one file in the batch is rejected", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing([shot]));
    vi.mocked(api.uploadFiles).mockResolvedValue(
      listing([shot], { errors: ["huge.bin is larger than the 64 MB limit"] }),
    );
    render(<UploadsPage />);
    await waitFor(() => expect(api.listUploads).toHaveBeenCalled());
    pick([new File(["x"], "shot.png"), new File(["y"], "huge.bin")]);
    await waitFor(() => expect(screen.getByText(/huge.bin is larger/)).toBeTruthy());
    // the one that made it is still listed, with its path
    expect(screen.getByText(shot.path)).toBeTruthy();
  });

  it("opens an upload in the file viewer", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing([shot]));
    render(<UploadsPage />);
    fireEvent.click(await screen.findByTitle("View shot.png"));
    expect(openFile).toHaveBeenCalledWith(shot.path);
  });

  it("deletes an upload and reloads the dir", async () => {
    vi.mocked(api.listUploads).mockResolvedValueOnce(listing([shot])).mockResolvedValue(listing());
    vi.mocked(api.deleteUpload).mockResolvedValue({ ok: true } as never);
    render(<UploadsPage />);
    fireEvent.click(await screen.findByTitle("Delete shot.png"));
    await waitFor(() => expect(api.deleteUpload).toHaveBeenCalledWith("shot.png"));
    await waitFor(() => expect(screen.getByText("Nothing uploaded yet.")).toBeTruthy());
  });

  it("uploads a pasted screenshot", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing());
    vi.mocked(api.uploadFiles).mockResolvedValue(listing([shot]));
    render(<UploadsPage />);
    await waitFor(() => expect(api.listUploads).toHaveBeenCalled());
    const file = new File(["x"], "pasted.png", { type: "image/png" });
    const ev = new Event("paste") as Event & { clipboardData?: unknown };
    Object.defineProperty(ev, "clipboardData", { value: { files: [file] } });
    fireEvent(window, ev);
    await waitFor(() => expect(api.uploadFiles).toHaveBeenCalledWith([file]));
  });

  it("scrolls: the root is the app's scroll container", async () => {
    vi.mocked(api.listUploads).mockResolvedValue(listing());
    const { container } = render(<UploadsPage />);
    await waitFor(() => expect(api.listUploads).toHaveBeenCalled());
    expect(container.firstElementChild?.classList.contains("list-wrap")).toBe(true);
  });

  it("says so when the listing fails instead of rendering an empty dir", async () => {
    vi.mocked(api.listUploads).mockRejectedValue(new Error("boom"));
    render(<UploadsPage />);
    expect(await screen.findByText("boom")).toBeTruthy();
    vi.mocked(api.listUploads).mockResolvedValue(listing());
  });
});
