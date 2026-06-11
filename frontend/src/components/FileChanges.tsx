import { useMemo, useState } from "react";
import type { FileChange, FileOp, FileOpKind } from "../api/types";

const OP_GLYPH: Record<FileOpKind, string> = {
  read: "○",
  edit: "✎",
  write: "✚",
};

function basename(path: string): { name: string; dir: string } {
  const i = path.lastIndexOf("/");
  return i < 0 ? { name: path, dir: "" } : { name: path.slice(i + 1), dir: path.slice(0, i + 1) };
}

interface Props {
  files: FileChange[];
  selectedToolId: string | null;
  onSelectOp: (toolUseId: string) => void;
}

/** Per-file activity view: which files the session touched and how. Clicking an
 * op selects its tool (opening the diff in the Detail pane + scroll-syncing). */
export default function FileChanges({ files, selectedToolId, onSelectOp }: Props) {
  // Auto-expand the file that owns the current selection.
  const selectedPath = useMemo(() => {
    if (!selectedToolId) return null;
    return files.find((f) => f.ops.some((o) => o.tool_use_id === selectedToolId))?.path ?? null;
  }, [files, selectedToolId]);

  const [open, setOpen] = useState<Set<string>>(new Set());
  const isOpen = (p: string) => open.has(p) || p === selectedPath;

  const toggle = (p: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(p) ? next.delete(p) : next.add(p);
      // If it was open only via selection, an explicit toggle should close it.
      if (!prev.has(p) && p === selectedPath) next.delete(p);
      return next;
    });

  if (files.length === 0) {
    return <div className="empty">No files were read or changed in this session.</div>;
  }

  return (
    <div className="fc">
      {files.map((f) => {
        const { name, dir } = basename(f.path);
        return (
          <div key={f.path} className="fc-file">
            <div
              className={`fc-row${f.error_count ? " has-error" : ""}`}
              onClick={() => toggle(f.path)}
            >
              <span className="fc-caret">{isOpen(f.path) ? "▾" : "▸"}</span>
              <span className="fc-name" title={f.path}>
                {dir && <span className="fc-dir">{dir}</span>}
                {name}
              </span>
              <span className="fc-badges">
                {f.read_count > 0 && <span className="fc-b read">R{f.read_count}</span>}
                {f.edit_count > 0 && <span className="fc-b edit">E{f.edit_count}</span>}
                {f.write_count > 0 && <span className="fc-b write">W{f.write_count}</span>}
                {f.error_count > 0 && <span className="fc-b err">⚠{f.error_count}</span>}
              </span>
            </div>
            {isOpen(f.path) && (
              <div className="fc-ops">
                {f.ops.map((o) => (
                  <OpRow
                    key={o.tool_use_id}
                    op={o}
                    selected={o.tool_use_id === selectedToolId}
                    onClick={() => onSelectOp(o.tool_use_id)}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function OpRow({
  op,
  selected,
  onClick,
}: {
  op: FileOp;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div
      className={`fc-op op-${op.kind}${selected ? " selected" : ""}${op.is_error ? " is-error" : ""}`}
      onClick={onClick}
    >
      <span className="fc-op-glyph">{OP_GLYPH[op.kind]}</span>
      <span className="fc-op-name">{op.tool_name}</span>
      {op.kind === "edit" && op.edit_count > 1 && (
        <span className="fc-op-count">×{op.edit_count}</span>
      )}
      {op.timestamp && (
        <span className="fc-op-time">
          {new Date(op.timestamp).toLocaleTimeString([], { hour12: false })}
        </span>
      )}
    </div>
  );
}
