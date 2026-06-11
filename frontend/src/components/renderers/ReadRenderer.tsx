import type { RendererProps } from "./types";
import CodeBlock from "../CodeBlock";
import Collapsible from "../Collapsible";
import ResultView from "./ResultView";
import { langForPath } from "../../util/highlight";

export default function ReadRenderer({ tool, sessionId }: RendererProps) {
  const path = tool.input.file_path ? String(tool.input.file_path) : undefined;
  const content = tool.result?.content ?? "";
  const lang = langForPath(path);

  return (
    <div>
      <div className="section-label">File</div>
      <pre className="code nowrap">{path ?? "(unknown)"}</pre>
      {tool.result && !tool.result.truncated && content ? (
        <>
          <div className="section-label">Contents</div>
          <Collapsible text={content}>
            <CodeBlock code={stripLineNumbers(content)} lang={lang} />
          </Collapsible>
        </>
      ) : (
        <ResultView result={tool.result} sessionId={sessionId} />
      )}
    </div>
  );
}

/** Read results come prefixed with `   123\t` line numbers; strip for highlighting. */
function stripLineNumbers(text: string): string {
  const lines = text.split("\n");
  const stripped = lines.map((l) => l.replace(/^\s*\d+\t/, ""));
  // Only strip if it actually looked numbered (avoid mangling plain text).
  const numbered = lines.filter((l) => /^\s*\d+\t/.test(l)).length;
  return numbered > lines.length / 2 ? stripped.join("\n") : text;
}
