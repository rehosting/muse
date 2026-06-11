import type { RendererProps } from "./types";
import ResultView from "./ResultView";

/** Grep / Glob: show the query + scoping fields, then results. */
export default function SearchRenderer({ tool, sessionId }: RendererProps) {
  const i = tool.input;
  const fields: Array<[string, string]> = [];
  if (i.pattern) fields.push(["pattern", String(i.pattern)]);
  if (i.path) fields.push(["path", String(i.path)]);
  if (i.glob) fields.push(["glob", String(i.glob)]);
  if (i.type) fields.push(["type", String(i.type)]);
  if (i.output_mode) fields.push(["output", String(i.output_mode)]);
  const flags = [i["-i"] && "-i", i["-n"] && "-n", i["-l"] && "-l"].filter(Boolean).join(" ");
  if (flags) fields.push(["flags", flags]);

  return (
    <div>
      <div className="kv-grid">
        {fields.map(([k, v]) => (
          <div className="kv-row" key={k}>
            <span className="kv-k">{k}</span>
            <span className="kv-v">{v}</span>
          </div>
        ))}
      </div>
      <ResultView result={tool.result} sessionId={sessionId} hint="plain" />
    </div>
  );
}
