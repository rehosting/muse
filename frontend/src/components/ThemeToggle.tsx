import { useEffect, useState } from "react";

const KEY = "muse.theme";

export function initTheme(): void {
  // Called before first render (main.tsx) so the page never flashes the wrong
  // theme. Default: dark (muse's native look).
  const saved = localStorage.getItem(KEY);
  document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";
}

/** Flip the theme from anywhere (e.g. the ⌘K palette); the navbar button
 * stays in sync via the muse:theme event. */
export function toggleTheme(): void {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem(KEY, next);
  window.dispatchEvent(new Event("muse:theme"));
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

  // Stay in sync when something else (the ⌘K palette) toggles the theme.
  useEffect(() => {
    const sync = () => setTheme(document.documentElement.dataset.theme ?? "dark");
    window.addEventListener("muse:theme", sync);
    return () => window.removeEventListener("muse:theme", sync);
  }, []);

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
