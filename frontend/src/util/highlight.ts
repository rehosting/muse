import { codeToHtml } from "shiki";

const EXT_LANG: Record<string, string> = {
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  py: "python",
  rs: "rust",
  go: "go",
  json: "json",
  md: "markdown",
  sh: "bash",
  bash: "bash",
  yml: "yaml",
  yaml: "yaml",
  toml: "toml",
  css: "css",
  html: "html",
  c: "c",
  h: "c",
  cpp: "cpp",
  java: "java",
  rb: "ruby",
  sql: "sql",
};

export function langForPath(path: string | undefined): string {
  if (!path) return "text";
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANG[ext] ?? "text";
}

export async function highlight(code: string, lang: string): Promise<string> {
  try {
    return await codeToHtml(code, { lang, theme: "github-dark" });
  } catch {
    // Unknown language — fall back to plain text highlighting.
    return await codeToHtml(code, { lang: "text", theme: "github-dark" });
  }
}
