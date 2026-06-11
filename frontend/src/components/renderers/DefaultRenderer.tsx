import type { RendererProps } from "./types";
import CodeBlock from "../CodeBlock";
import Collapsible from "../Collapsible";
import ResultView from "./ResultView";

export default function DefaultRenderer({ tool, sessionId }: RendererProps) {
  const inputJson = JSON.stringify(tool.input, null, 2);
  return (
    <div>
      <div className="section-label">Input</div>
      <Collapsible text={inputJson}>
        <CodeBlock code={inputJson} lang="json" />
      </Collapsible>
      <ResultView result={tool.result} sessionId={sessionId} />
    </div>
  );
}
