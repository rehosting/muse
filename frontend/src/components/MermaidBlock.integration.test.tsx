/** Integration: the REAL mermaid build, not a mock. The unit tests mock the module
 * (they cover muse's wiring); this one pins that the installed mermaid actually turns
 * our fence syntax into an SVG, and that invalid input rejects so MermaidBlock's
 * fall-back-to-source path is reachable rather than theoretical. */
import { beforeAll, describe, expect, it } from "vitest";

const FLOW = "flowchart TD\n  A[Start] --> B{Ok?}\n  B -->|yes| C[Done]\n  B -->|no| A";

describe("mermaid (real library)", () => {
  beforeAll(() => {
    // jsdom has no SVG layout engine; mermaid only needs these to measure labels.
    // Geometry will be nonsense, but parse → layout → serialize still exercises.
    (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () =>
      ({ x: 0, y: 0, width: 100, height: 20 }) as DOMRect;
    (SVGElement.prototype as unknown as { getComputedTextLength: () => number }).getComputedTextLength =
      () => 100;
  });
  it("turns flowchart syntax into an svg with the node labels intact", async () => {
    const mermaid = (await import("mermaid")).default;
    mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
    const { svg, diagramType } = await mermaid.render("t1", FLOW);
    expect(diagramType).toBe("flowchart-v2");
    expect(svg).toContain("<svg");
    for (const label of ["Start", "Done", "yes"]) expect(svg).toContain(label);
  }, 60000);

  it("rejects invalid syntax (so the source fallback actually triggers)", async () => {
    const mermaid = (await import("mermaid")).default;
    mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
    await expect(mermaid.render("t2", "flowchart TD\n  A --> -->")).rejects.toThrow();
  }, 60000);
});
