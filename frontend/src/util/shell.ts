/** Quote a path for safe use in a POSIX shell command. */
export function shellQuote(value: string): string {
  if (value === "") return "''";
  if (/^[A-Za-z0-9_/.:@%+=-]+$/.test(value)) return value;
  // Wrap in single quotes, escaping embedded single quotes.
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/** The command to resume a Claude Code session in its working directory. */
export function resumeCommand(cwd: string | null, sessionId: string): string {
  const dir = cwd ? shellQuote(cwd) : "~";
  return `cd ${dir} && claude --resume ${sessionId}`;
}

/** Copy text to the clipboard, with a fallback for older browsers. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}
