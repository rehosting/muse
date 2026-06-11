import { useEffect, useState } from "react";

const KEY = "muse.theme";

export function initTheme(): void {
  // Called before first render (main.tsx) so the page never flashes the wrong
  // theme. Default: dark (muse's native look).
  const saved = localStorage.getItem(KEY);
  document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";
}

/** ☀/🌙 switch in the navbar. The conversation reconstruction stays terminal-
 * dark in both themes (it's an embedded Claude Code pane). */
export default function ThemeToggle() {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme ?? "dark",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(KEY, theme);
  }, [theme]);

  return (
    <button
      className="theme-toggle"
      title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
    >
      {theme === "dark" ? "☀" : "🌙"}
    </button>
  );
}
