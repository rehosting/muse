// Reconstructs Claude Code's *inline* scrollback rendering for tool calls — the
// `⎿` body shown directly under each `⏺ Tool(arg)` line in the terminal. This is
// deliberately separate from the side-panel renderers (renderers/*), which are
// rich/interactive; here we mirror exactly what the CLI prints in the transcript.

import type { ToolUse } from "../api/types";

/** A single rendered line of a tool's inline body. `cls` styles it (diff add/del,
 *  dim context, error, todo state); `text` is the literal line. */
export interface ToolRow {
  cls?: string;
  text: string;
}

export interface ToolBody {
  rows: ToolRow[];
  /** How many rows to show before the "… +N lines" fold. */
  limit: number;
}

/** CC shows friendlier verbs than the raw tool name for a few tools. */
export function toolTitle(name: string): string {
  if (name === "Edit" || name === "MultiEdit") return "Update";
  if (name === "TodoWrite") return "Update Todos";
  return name;
}

/** Primary arg shown in `Tool(arg)`. TodoWrite has no arg (it's "Update Todos"). */
export function toolArg(tool: ToolUse): string {
  const input = tool.input;
  switch (tool.name) {
    case "TodoWrite":
      return "";
    case "Bash":
    case "BashOutput":
      return String(input.command ?? "");
    case "Read":
    case "Edit":
    case "MultiEdit":
    case "Write":
      return String(input.file_path ?? "");
    case "Task":
    case "Agent":
      return String(input.description ?? input.subagent_type ?? "");
    case "Grep":
    case "Glob":
      return String(input.pattern ?? "");
    case "WebFetch":
      return String(input.url ?? "");
    case "WebSearch":
      return String(input.query ?? "");
    default: {
      const first = Object.values(input)[0];
      return first !== undefined ? String(first).slice(0, 120) : "";
    }
  }
}

function base(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

interface EditPair {
  old: string;
  next: string;
}

function extractEdits(input: Record<string, unknown>): EditPair[] {
  if (Array.isArray(input.edits)) {
    return (input.edits as Record<string, unknown>[]).map((e) => ({
      old: String(e.old_string ?? ""),
      next: String(e.new_string ?? ""),
    }));
  }
  return [{ old: String(input.old_string ?? ""), next: String(input.new_string ?? "") }];
}

/** Minimal unified line-diff: trim the common prefix/suffix, mark the middle of
 *  `old` as removed and the middle of `next` as added, with ≤3 lines of context
 *  on each side — the shape CC prints for an Edit (sans absolute line numbers,
 *  which the transcript doesn't carry). */
function lineDiff(oldStr: string, newStr: string): { rows: ToolRow[]; add: number; del: number } {
  const a = oldStr ? oldStr.split("\n") : [];
  const b = newStr ? newStr.split("\n") : [];
  let s = 0;
  while (s < a.length && s < b.length && a[s] === b[s]) s++;
  let ea = a.length;
  let eb = b.length;
  while (ea > s && eb > s && a[ea - 1] === b[eb - 1]) {
    ea--;
    eb--;
  }
  const rows: ToolRow[] = [];
  for (let i = Math.max(0, s - 3); i < s; i++) rows.push({ cls: "cc-diff-ctx", text: `  ${a[i]}` });
  for (let i = s; i < ea; i++) rows.push({ cls: "cc-diff-del", text: `- ${a[i]}` });
  for (let i = s; i < eb; i++) rows.push({ cls: "cc-diff-add", text: `+ ${b[i]}` });
  for (let i = ea; i < Math.min(a.length, ea + 3); i++)
    rows.push({ cls: "cc-diff-ctx", text: `  ${a[i]}` });
  return { rows, add: eb - s, del: ea - s };
}

function splitRows(text: string, cls?: string): ToolRow[] {
  return (text.length ? text.split("\n") : []).map((t) => ({ cls, text: t }));
}

/** Build the inline `⎿` body for a tool call, matching CC's per-tool output. */
export function toolBody(tool: ToolUse): ToolBody {
  const result = tool.result;
  const content = result?.content ?? result?.preview ?? "";

  if (result?.is_error) {
    return { rows: splitRows(content || "Error", "cc-row-err"), limit: 8 };
  }

  switch (tool.name) {
    case "Edit":
    case "MultiEdit": {
      const edits = extractEdits(tool.input);
      let add = 0;
      let del = 0;
      const diff: ToolRow[] = [];
      for (const e of edits) {
        const d = lineDiff(e.old, e.next);
        add += d.add;
        del += d.del;
        diff.push(...d.rows);
      }
      const path = base(String(tool.input.file_path ?? ""));
      const summary = `Updated ${path} with ${plural(add, "addition")} and ${plural(del, "removal")}`;
      return { rows: [{ text: summary }, ...diff], limit: 9 };
    }
    case "Write": {
      const body = String(tool.input.content ?? "");
      const n = body ? body.split("\n").length : 0;
      const path = base(String(tool.input.file_path ?? ""));
      return {
        rows: [{ text: `Wrote ${plural(n, "line")} to ${path}` }, ...splitRows(body, "cc-diff-add")],
        limit: 9,
      };
    }
    case "Read": {
      const n = content ? content.split("\n").length : 0;
      return { rows: [{ text: `Read ${plural(n, "line")}` }, ...splitRows(content, "cc-dim")], limit: 1 };
    }
    case "TodoWrite": {
      const todos = Array.isArray(tool.input.todos)
        ? (tool.input.todos as Array<Record<string, unknown>>)
        : [];
      const rows = todos.map((t) => {
        const status = String(t.status ?? "pending");
        const box = status === "completed" ? "☒" : "☐";
        const text = String(
          status === "in_progress" && t.activeForm ? t.activeForm : t.content ?? "",
        );
        return { cls: `cc-todo cc-todo-${status}`, text: `${box} ${text}` };
      });
      return { rows: rows.length ? rows : [{ text: "(no todos)" }], limit: 100 };
    }
    case "Bash":
    case "BashOutput":
      return { rows: splitRows(content || "(no output)"), limit: 8 };
    default:
      return { rows: splitRows(content || "(No content)"), limit: 6 };
  }
}

// ---- user-turn cleanup: slash commands, command output, injected meta ----

export type UserRender =
  | { kind: "hidden" }
  | { kind: "command"; name: string; args: string }
  | { kind: "stdout"; text: string }
  | { kind: "text"; text: string };

function strip(text: string, open: string, close: string): string {
  const re = new RegExp(`${open}[\\s\\S]*?${close}`, "g");
  return text.replace(re, "");
}

/** Decide how a user line should render, mirroring the CLI: slash commands show
 *  as `> /cmd`, their stdout as a dim block, and harness-injected wrappers
 *  (caveat, system-reminder) are hidden the way the terminal hides them. */
export function classifyUser(raw: string): UserRender {
  const cmd = /<command-name>([\s\S]*?)<\/command-name>/.exec(raw);
  if (cmd) {
    const args = /<command-args>([\s\S]*?)<\/command-args>/.exec(raw);
    return { kind: "command", name: cmd[1].trim(), args: (args?.[1] ?? "").trim() };
  }
  const out = /<local-command-stdout>([\s\S]*?)<\/local-command-stdout>/.exec(raw);
  if (out) {
    const text = out[1].trim();
    return text ? { kind: "stdout", text } : { kind: "hidden" };
  }
  // Drop harness-injected wrappers the CLI never shows in scrollback.
  let rest = strip(raw, "<system-reminder>", "</system-reminder>");
  rest = strip(rest, "<local-command-caveat>", "</local-command-caveat>");
  rest = rest.trim();
  if (!rest) return { kind: "hidden" };
  return { kind: "text", text: rest };
}
