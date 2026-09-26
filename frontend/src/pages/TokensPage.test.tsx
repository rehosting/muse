/** Tokens page: renders the tracker's numbers, and degrades to an actionable message
 * when the CLI isn't available (a missing tool is an environment condition, not a bug). */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({ api: { getTokenUsage: vi.fn() } }));
import { api } from "../api/client";
import TokensPage from "./TokensPage";

const usage = (over = {}) =>
  ({
    days: 7,
    refreshed: false,
    generated_at: "2026-09-26T12:00:00Z",
    available: true,
    session_count: 2,
    summary: {
      cost_usd: 2063.83, total_tokens: 2_775_122_052, edit_turns: 96,
      retries: 1, cost_per_edit: 13.5, first_pass_rate: 0.75, productive_rate: 0.258,
    },
    by_model: [
      { model: "claude-opus-4-8", sessions: 7, total_tokens: 986_734_474, cost_usd: 677.97, edit_turns: 50, first_pass_rate: 0.714 },
    ],
    subagents: [{ name: "general-purpose", calls: 11, sessions: 4, total_tokens: 29_813_280, cost_usd: 21.39 }],
    sessions: [
      { session_hash: "a1", source: "codex", project_key: "kernel-lift-c", model: "gpt-6-astra", turns: 9, edit_turns: 3, total_tokens: 15_364_755, cost_usd: 34.88, started_at: "2026-09-26T12:51:57Z" },
      { session_hash: "b2", source: "claude", project_key: "muse", model: "claude-opus-5", turns: 4, edit_turns: 1, total_tokens: 1_000_000, cost_usd: 2.5, started_at: "2026-09-26T09:00:00Z" },
    ],
    provenance: { privacy: "metadata-only" },
    ...over,
  }) as never;

describe("TokensPage", () => {
  it("shows the tracker's headline numbers and tables", async () => {
    vi.mocked(api.getTokenUsage).mockResolvedValue(usage());
    render(<TokensPage />);
    await waitFor(() => expect(screen.getByText("$2.1k")).toBeTruthy());
    expect(screen.getByText("2.78B")).toBeTruthy(); // tokens, humanized
    expect(screen.getByText("75%")).toBeTruthy(); // first-pass rate
    expect(screen.getByText("claude-opus-4-8")).toBeTruthy();
    expect(screen.getByText("general-purpose")).toBeTruthy();
    expect(screen.getByText("kernel-lift-c")).toBeTruthy();
  });

  it("orders the session table by spend, not by arrival", async () => {
    vi.mocked(api.getTokenUsage).mockResolvedValue(usage());
    const { container } = render(<TokensPage />);
    await waitFor(() => expect(screen.getByText("kernel-lift-c")).toBeTruthy());
    const tables = [...container.querySelectorAll("table")];
    const projects = tables[tables.length - 1].querySelectorAll("tbody tr td:first-child");
    expect([...projects].map((td) => td.textContent)).toEqual(["kernel-lift-c", "muse"]);
  });

  it("re-parses only when asked, and says so while it runs", async () => {
    vi.mocked(api.getTokenUsage).mockResolvedValue(usage());
    render(<TokensPage />);
    await waitFor(() => expect(api.getTokenUsage).toHaveBeenCalledWith(7, false));
    fireEvent.click(screen.getByTitle(/Re-read every transcript/));
    await waitFor(() => expect(api.getTokenUsage).toHaveBeenCalledWith(7, true));
  });

  it("switches the window without a full-page error", async () => {
    vi.mocked(api.getTokenUsage).mockResolvedValue(usage({ days: 30 }));
    render(<TokensPage />);
    await waitFor(() => expect(api.getTokenUsage).toHaveBeenCalledWith(7, false));
    fireEvent.click(screen.getByText("30d"));
    await waitFor(() => expect(api.getTokenUsage).toHaveBeenCalledWith(30, false));
  });

  it("scrolls: the root is the app's scroll container", async () => {
    // The session table can run to hundreds of rows; without .list-wrap the page is
    // clipped by body{overflow:hidden} and none of it is reachable.
    vi.mocked(api.getTokenUsage).mockResolvedValue(usage());
    const { container } = render(<TokensPage />);
    await waitFor(() => expect(screen.getByText("$2.1k")).toBeTruthy());
    expect(container.querySelector(".list-wrap.tokens-page")).toBeTruthy();
  });

  it("explains how to install the CLI when it's missing", async () => {
    vi.mocked(api.getTokenUsage).mockRejectedValue(new Error("tokentracker CLI not found"));
    render(<TokensPage />);
    await waitFor(() => expect(screen.getByText(/tokentracker CLI not found/)).toBeTruthy());
    expect(screen.getByText(/npm i -g tokentracker-cli/)).toBeTruthy();
  });
});
