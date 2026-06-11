import type { RendererProps } from "./types";
import ResultView from "./ResultView";

/** WebFetch / WebSearch: show url/query + prompt, then the result as markdown. */
export default function WebRenderer({ tool, sessionId }: RendererProps) {
  const i = tool.input;
  const url = i.url ? String(i.url) : null;
  const query = i.query ? String(i.query) : null;
  const prompt = i.prompt ? String(i.prompt) : null;

  return (
    <div>
      {url && (
        <>
          <div className="section-label">URL</div>
          <pre className="code nowrap">
            <a href={url} target="_blank" rel="noreferrer noopener">
              {url}
            </a>
          </pre>
        </>
      )}
      {query && (
        <>
          <div className="section-label">Query</div>
          <pre className="code">{query}</pre>
        </>
      )}
      {prompt && (
        <>
          <div className="section-label">Prompt</div>
          <pre className="code">{prompt}</pre>
        </>
      )}
      <ResultView result={tool.result} sessionId={sessionId} hint="markdown" />
    </div>
  );
}
