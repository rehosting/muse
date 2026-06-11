import { formatTokens } from "../util/format";

/** Compact bar showing how full the model's context window is. */
export default function ContextMeter({
  used,
  window,
  pct,
}: {
  used: number;
  window: number;
  pct: number;
}) {
  const level = pct >= 85 ? "high" : pct >= 60 ? "mid" : "low";
  return (
    <span
      className="ctx-meter"
      title={`Context window used: ${used.toLocaleString()} / ${window.toLocaleString()} tokens (${pct.toFixed(1)}%)`}
    >
      <span className="ctx-label">ctx</span>
      <span className="ctx-bar">
        <span className={`ctx-fill ctx-${level}`} style={{ width: `${Math.max(2, pct)}%` }} />
      </span>
      <span className="ctx-text">
        {formatTokens(used)} / {formatTokens(window)}
        <span className="cc-dim"> ({Math.round(pct)}%)</span>
      </span>
    </span>
  );
}
