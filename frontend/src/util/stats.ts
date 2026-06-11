import type { Thread, Usage } from "../api/types";

export interface SessionStats {
  model: string | null;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheTokens: number;
  /** Context occupancy of the most recent turn (input + cache read + cache creation). */
  contextUsed: number;
  /** Inferred context window for the model. */
  contextWindow: number;
  contextPct: number;
}

/** Tokens occupying the context window for a single turn. */
function turnContext(u: Usage): number {
  return u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens;
}

/**
 * The transcript records the model id but not whether the 1M-context beta was on.
 * Infer the window from the largest context actually observed: if any turn ever
 * exceeded the 200k standard window, this must be a 1M-context session.
 */
function contextWindowFor(peakContext: number): number {
  const tiers = [200_000, 1_000_000];
  for (const t of tiers) if (peakContext <= t) return t;
  return tiers[tiers.length - 1];
}

export function sessionStats(thread: Thread): SessionStats {
  const u = thread.usage_total;
  const inputTokens = u.input_tokens;
  const outputTokens = u.output_tokens;
  const cacheTokens = u.cache_creation_input_tokens + u.cache_read_input_tokens;
  const totalTokens = inputTokens + outputTokens + cacheTokens;

  let model: string | null = null;
  let peak = 0;
  let lastContext = 0;
  for (const item of thread.items) {
    if (item.model) model = item.model;
    if (item.usage) {
      const c = turnContext(item.usage);
      if (c > peak) peak = c;
      lastContext = c; // last turn with usage reflects current occupancy
    }
  }

  const contextWindow = contextWindowFor(peak);
  const contextPct = contextWindow ? Math.min(100, (lastContext / contextWindow) * 100) : 0;

  return {
    model,
    totalTokens,
    inputTokens,
    outputTokens,
    cacheTokens,
    contextUsed: lastContext,
    contextWindow,
    contextPct,
  };
}
