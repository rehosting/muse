import type { Usage } from "../api/types";
import { formatTokens } from "../util/format";

export default function UsageStats({ usage }: { usage: Usage }) {
  if (!usage) return null;
  return (
    <span className="usage-stats" title="Token usage">
      <span>in {formatTokens(usage.input_tokens)}</span>
      <span>out {formatTokens(usage.output_tokens)}</span>
      {usage.cache_read_input_tokens > 0 && (
        <span>cache {formatTokens(usage.cache_read_input_tokens)}</span>
      )}
    </span>
  );
}
