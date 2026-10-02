import { useEffect, useRef, useState } from "react";

/**
 * Renders a ```mermaid fence as a diagram.
 *
 * mermaid is ~3MB of parser and layout code, and the conversation view mounts
 * Markdown for every message — so it is imported DYNAMICALLY and only when a
 * diagram is actually on screen. Threads without diagrams never pay for it, and
 * the import is shared across every block on the page.
 *
 * A broken diagram must never take the message with it: syntax errors fall back
 * to the original fenced source, which is what the user typed anyway.
 */

let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((m) => m.default);
  }
  return mermaidPromise;
}

let seq = 0;
const nextId = () => `mermaid-${Date.now().toString(36)}-${seq++}`;

function currentTheme(): "dark" | "default" {
  return document.documentElement.dataset.theme === "light" ? "default" : "dark";
}

export default function MermaidBlock({ code }: { code: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [theme, setTheme] = useState(currentTheme);
  const idRef = useRef<string>(nextId());

  // Diagrams are baked at render time, so a theme flip has to re-bake them.
  useEffect(() => {
    const sync = () => setTheme(currentTheme());
    window.addEventListener("muse:theme", sync);
    return () => window.removeEventListener("muse:theme", sync);
  }, []);

  useEffect(() => {
    let alive = true;
    setFailed(null);
    loadMermaid()
      .then(async (mermaid) => {
        mermaid.initialize({
          startOnLoad: false,
          theme,
          // Labels come from transcripts, i.e. model output — never let a diagram
          // inject markup or scripts into the page.
          securityLevel: "strict",
          fontFamily: "inherit",
        });
        const { svg: out } = await mermaid.render(idRef.current, code);
        if (alive) setSvg(out);
      })
      .catch((e: unknown) => {
        if (alive) setFailed(e instanceof Error ? e.message : "could not render diagram");
      });
    return () => {
      alive = false;
    };
  }, [code, theme]);

  if (failed !== null) {
    // Show what they wrote plus why it didn't draw — more useful than an error box.
    return (
      <div className="mermaid-failed">
        <div className="mermaid-failed-msg" title={failed}>
          diagram didn’t render — showing source
        </div>
        <pre>
          <code>{code}</code>
        </pre>
      </div>
    );
  }

  if (svg === null) return <div className="mermaid-pending">rendering diagram…</div>;

  return (
    <div
      className="mermaid-block"
      role="img"
      aria-label="diagram"
      // mermaid output, generated under securityLevel:"strict" (labels sanitized).
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
