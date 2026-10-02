/** ```mermaid fences render as diagrams; every other fence stays source. The real
 * mermaid module is mocked — jsdom has no SVG layout, and what's under test here is
 * muse's wiring, not mermaid's renderer. */
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const renderFn = vi.fn(async (_id: string, code: string) => ({
  svg: `<svg data-testid="diagram"><title>${code.split("\n")[0]}</title></svg>`,
}));
const initialize = vi.fn();
vi.mock("mermaid", () => ({ default: { initialize, render: renderFn } }));

import Markdown from "./Markdown";

const FLOW = ["```mermaid", "flowchart TD", "  A[Start] --> B{Ok?}", "  B -->|yes| C[Done]", "```"].join("\n");

describe("Markdown mermaid support", () => {
  it("renders a mermaid fence as a diagram", async () => {
    render(<Markdown>{FLOW}</Markdown>);
    await waitFor(() => expect(screen.getByTestId("diagram")).toBeTruthy());
    // The fence's source reached mermaid intact (not highlighted into elements).
    expect(renderFn).toHaveBeenCalled();
    expect(renderFn.mock.calls[0][1]).toContain("flowchart TD");
    expect(renderFn.mock.calls[0][1]).toContain("B -->|yes| C[Done]");
  });

  it("renders diagrams under the strict security level", async () => {
    render(<Markdown>{FLOW}</Markdown>);
    await waitFor(() => expect(initialize).toHaveBeenCalled());
    // Diagram text is model output; label HTML must never be trusted.
    expect(initialize.mock.calls[0][0].securityLevel).toBe("strict");
  });

  it("leaves other fenced languages as code", () => {
    const { container } = render(<Markdown>{"```python\nprint(1)\n```"}</Markdown>);
    expect(container.querySelector("code")).toBeTruthy();
    expect(container.querySelector('[data-testid="diagram"]')).toBeNull();
  });

  it("leaves inline code alone", () => {
    const { container } = render(<Markdown>{"use `mermaid` for diagrams"}</Markdown>);
    expect(container.querySelector("code")?.textContent).toBe("mermaid");
    expect(container.querySelector('[data-testid="diagram"]')).toBeNull();
  });

  it("falls back to the source when a diagram is invalid", async () => {
    renderFn.mockRejectedValueOnce(new Error("Parse error on line 2"));
    render(<Markdown>{"```mermaid\nnot a diagram\n```"}</Markdown>);
    await waitFor(() => expect(screen.getByText(/didn’t render/)).toBeTruthy());
    expect(screen.getByText("not a diagram")).toBeTruthy();
  });
});
