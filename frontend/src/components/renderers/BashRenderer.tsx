import type { RendererProps } from "./types";
import CodeBlock from "../CodeBlock";
import ResultView from "./ResultView";

export default function BashRenderer({ tool, sessionId }: RendererProps) {
  const cmd = String(tool.input.command ?? "");
  const desc = tool.input.description ? String(tool.input.description) : null;
  return (
    <div>
      {desc && <div className="section-label">{desc}</div>}
      <div className="section-label">Command</div>
      <CodeBlock code={cmd} lang="bash" />
      <ResultView result={tool.result} sessionId={sessionId} hint="bash" />
    </div>
  );
}
