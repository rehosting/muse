import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";

function load(key: string, n: number): number[] {
  try {
    const v = JSON.parse(localStorage.getItem(key) || "");
    if (Array.isArray(v) && v.length === n && v.every((x) => typeof x === "number")) return v;
  } catch {
    /* ignore */
  }
  return Array.from({ length: n }, () => 1 / n);
}

/**
 * Flex split with draggable gutters between children. Sizes (grow ratios) are
 * persisted per storageKey. Works for rows and columns; can be nested.
 */
export default function ResizableSplit({
  direction,
  storageKey,
  children,
}: {
  direction: "row" | "col";
  storageKey: string;
  children: ReactNode[];
}) {
  const items = children.filter(Boolean);
  const n = items.length;
  const ref = useRef<HTMLDivElement>(null);
  const [sizes, setSizes] = useState<number[]>(() => load(storageKey, n));

  useEffect(() => {
    setSizes(load(storageKey, n));
  }, [storageKey, n]);

  const horizontal = direction === "row";

  const onDown = (i: number, e: React.MouseEvent) => {
    e.preventDefault();
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const total = horizontal ? rect.width : rect.height;
    const startPos = horizontal ? e.clientX : e.clientY;
    const base = [...sizes];
    const min = 0.08;

    const move = (ev: MouseEvent) => {
      const pos = horizontal ? ev.clientX : ev.clientY;
      let d = (pos - startPos) / total;
      d = Math.max(-(base[i] - min), Math.min(base[i + 1] - min, d));
      const next = [...base];
      next[i] = base[i] + d;
      next[i + 1] = base[i + 1] - d;
      setSizes(next);
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.classList.remove("resizing");
      setSizes((s) => {
        localStorage.setItem(storageKey, JSON.stringify(s));
        return s;
      });
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    document.body.classList.add("resizing");
  };

  return (
    <div ref={ref} className={`split split-${direction}`}>
      {items.map((c, i) => (
        <Fragment key={i}>
          <div className="split-cell" style={{ flexGrow: sizes[i], flexBasis: 0 }}>
            {c}
          </div>
          {i < n - 1 && (
            <div className={`gutter gutter-${direction}`} onMouseDown={(e) => onDown(i, e)} />
          )}
        </Fragment>
      ))}
    </div>
  );
}
