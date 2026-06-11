import ReactDiffViewer, { DiffMethod } from "react-diff-viewer-continued";
import type { RendererProps } from "./types";
import ResultView from "./ResultView";

interface EditPair {
  old: string;
  next: string;
}

function extractEdits(input: Record<string, unknown>): EditPair[] {
  // MultiEdit: input.edits = [{old_string, new_string}, ...]
  if (Array.isArray(input.edits)) {
    return (input.edits as Record<string, unknown>[]).map((e) => ({
      old: String(e.old_string ?? ""),
      next: String(e.new_string ?? ""),
    }));
  }
  // Single Edit / Write
  return [
    {
      old: String(input.old_string ?? ""),
      next: String(input.new_string ?? input.content ?? ""),
    },
  ];
}

export default function EditRenderer({ tool, sessionId }: RendererProps) {
  const path = tool.input.file_path ? String(tool.input.file_path) : "(unknown)";
  const edits = extractEdits(tool.input);

  return (
    <div>
      <div className="section-label">File</div>
      <pre className="code nowrap">{path}</pre>
      {edits.map((e, i) => (
        <div key={i} style={{ margin: "8px 0", fontSize: 12 }}>
          {edits.length > 1 && <div className="section-label">Edit {i + 1}</div>}
          <ReactDiffViewer
            oldValue={e.old}
            newValue={e.next}
            splitView={false}
            compareMethod={DiffMethod.WORDS}
            useDarkTheme
          />
        </div>
      ))}
      <ResultView result={tool.result} sessionId={sessionId} />
    </div>
  );
}
