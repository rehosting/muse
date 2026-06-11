import { useState, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Collapse when the rendered text exceeds this many characters. */
  threshold?: number;
  text?: string;
  collapsedLabel?: string;
}

export default function Collapsible({
  children,
  threshold = 1200,
  text = "",
  collapsedLabel = "Show full output",
}: Props) {
  const shouldCollapse = text.length > threshold;
  const [open, setOpen] = useState(!shouldCollapse);

  if (!shouldCollapse) return <>{children}</>;

  return (
    <div>
      {open ? (
        children
      ) : (
        <pre className="code" style={{ maxHeight: 160, overflow: "hidden" }}>
          {text.slice(0, threshold)}…
        </pre>
      )}
      <button className="collapsible-toggle" onClick={() => setOpen(!open)}>
        {open ? "Collapse" : `${collapsedLabel} (${text.length.toLocaleString()} chars)`}
      </button>
    </div>
  );
}
