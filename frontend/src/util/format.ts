export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function shortModel(model: string | null): string {
  if (!model) return "";
  if (model.startsWith("claude-")) return model.replace(/^claude-/, "").replace(/-\d{8}$/, "");
  return model; // other providers (gpt-5.5, gemini-2.0-flash, …) read fine as-is
}

/** "claude-opus-4-8" -> "Opus 4.8"; "claude-haiku-4-5-20251001" -> "Haiku 4.5".
 * Non-Claude model ids are returned unchanged (they're already readable). */
export function modelDisplay(model: string | null): string {
  if (!model) return "";
  if (!model.startsWith("claude-")) return model;
  const parts = model.replace(/^claude-/, "").replace(/-\d{8}$/, "").split("-");
  if (parts.length === 0) return model;
  const family = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
  const ver = parts.slice(1).join(".");
  return ver ? `${family} ${ver}` : family;
}

/** Context window size -> "1M context" / "200K context". */
export function contextLabel(tokens: number): string {
  if (tokens >= 1_000_000) return `${tokens / 1_000_000}M context`;
  return `${Math.round(tokens / 1000)}K context`;
}

/** Abbreviate the user's home dir to ~ for display. */
export function abbrevHome(path: string | null): string {
  if (!path) return "";
  return path.replace(/^\/home\/[^/]+/, "~").replace(/^\/Users\/[^/]+/, "~");
}

export function formatUSD(n: number): string {
  if (n >= 100) return `$${n.toFixed(0)}`;
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(3)}`;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
}
