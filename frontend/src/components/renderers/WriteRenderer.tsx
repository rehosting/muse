import type { RendererProps } from "./types";
import CodeBlock from "../CodeBlock";
import Collapsible from "../Collapsible";
import ResultView from "./ResultView";
import FilePathLink from "../FilePathLink";
import { langForPath } from "../../util/highlight";

/** Write: show the full new file content, syntax-highlighted by extension. */
export default function WriteRenderer({ tool, sessionId }: RendererProps) {
  const path = tool.input.file_path ? String(tool.input.file_path) : undefined;
  const content = String(tool.input.content ?? "");
  return (
    <div>
      <div className="section-label">New file</div>
      <FilePathLink path={path} />
      <div className="section-label">Content</div>
      <Collapsible text={content}>
        <CodeBlock code={content} lang={langForPath(path)} />
      </Collapsible>
      <ResultView result={tool.result} sessionId={sessionId} hint="plain" />
    </div>
  );
}
