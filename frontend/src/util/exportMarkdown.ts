import type { Thread, ThreadItem } from "../api/types";
import { summarize } from "../components/renderers";
import { shortModel } from "./format";

function fence(text: string, lang = ""): string {
  // Use a longer fence if the body itself contains triple backticks.
  const ticks = text.includes("```") ? "````" : "```";
  return `${ticks}${lang}\n${text}\n${ticks}`;
}

function h(level: number, text: string): string {
  return `${"#".repeat(Math.min(level, 6))} ${text}`;
}

/** Render an assistant item's tool calls. When a tool spawned a subagent and we
 * have that subagent's thread, inline its full transcript right here (collapsible,
 * one heading level deeper) so the session reads as one cogent document. */
function toolsToMarkdown(
  item: ThreadItem,
  level: number,
  subagents: Map<string, Thread>,
  seen: Set<string>,
): string[] {
  const out: string[] = [];
  for (const b of item.blocks) {
    if (b.kind !== "tool_use" || !b.tool_use) continue;
    const t = b.tool_use;
    out.push(`**🔧 ${t.name}** \`${summarize(t.name, t.input)}\``);
    const r = t.result;
    if (r) {
      if (r.truncated) {
        out.push(fence(r.preview ?? "(output truncated — open in muse for full output)"));
      } else if (r.content) {
        out.push(fence(r.content));
      }
      if (r.is_error) out.push("> ⚠️ tool returned an error");
    }
    if (t.subagent) {
      const sub = subagents.get(t.subagent.agent_id);
      if (sub && !seen.has(t.subagent.agent_id)) {
        const nseen = new Set(seen).add(t.subagent.agent_id);
        out.push("");
        out.push(
          `<details><summary>🧵 Subagent: <code>${t.subagent.agent_type}</code> — ` +
            `${t.subagent.description} (${sub.items.length} msgs)</summary>`,
        );
        out.push("");
        out.push(...renderThread(sub, level + 1, subagents, nseen));
        out.push("");
        out.push("</details>");
      } else {
        out.push(
          `> 🧵 subagent: \`${t.subagent.agent_type}\` — ${t.subagent.description}` +
            (sub ? "" : " _(transcript unavailable)_"),
        );
      }
    }
  }
  return out;
}

/** Render one thread's items at a given heading level. `subagents` maps agent_id →
 * thread for inlining; `seen` guards against re-inlining/cycles. */
function renderThread(
  thread: Thread,
  level: number,
  subagents: Map<string, Thread>,
  seen: Set<string>,
): string[] {
  const lines: string[] = [];
  for (const item of thread.items) {
    if (item.role === "user") {
      if (!item.text) continue; // tool-result-only carrier lines
      lines.push(h(level, "🧑 User"), "", item.text, "");
    } else if (item.role === "assistant") {
      const model = item.model ? ` (${shortModel(item.model)})` : "";
      lines.push(h(level, `🤖 Assistant${model}`), "");
      for (const b of item.blocks) {
        if (b.kind === "text" && b.text) {
          lines.push(b.text, "");
        } else if (b.kind === "thinking" && b.text) {
          lines.push(`<details><summary>💭 thinking</summary>\n\n${b.text}\n\n</details>`, "");
        }
      }
      const tools = toolsToMarkdown(item, level, subagents, seen);
      if (tools.length) lines.push(...tools, "");
    } else if (item.role === "system" && item.text) {
      lines.push(`> ℹ️ ${item.text.replace(/\n/g, "\n> ")}`, "");
    }
  }
  return lines;
}

function docHeader(thread: Thread, subagentCount: number): string[] {
  const u = thread.usage_total;
  const lines: string[] = [`# ${thread.title}`, ""];
  lines.push(`**Session**: \`${thread.session_id}\``);
  if (thread.project_cwd) lines.push(`**Project**: \`${thread.project_cwd}\``);
  if (thread.agent_type) lines.push(`**Subagent**: ${thread.agent_type}`);
  lines.push(
    `**Tokens**: ${u.input_tokens.toLocaleString()} in / ${u.output_tokens.toLocaleString()} out`,
  );
  if (subagentCount > 0) {
    lines.push(`**Subagents**: ${subagentCount} (inlined at their spawn points)`);
  }
  lines.push("", "---", "");
  return lines;
}

/** A single thread → Markdown (no subagents inlined). */
export function threadToMarkdown(thread: Thread): string {
  return [...docHeader(thread, 0), ...renderThread(thread, 2, new Map(), new Set())].join("\n");
}

/** A session + all its subagents → one cogent Markdown document, each subagent's
 * transcript inlined (collapsible) at the step that spawned it, recursively. */
export function sessionTreeToMarkdown(main: Thread, subagents: Map<string, Thread>): string {
  return [
    ...docHeader(main, subagents.size),
    ...renderThread(main, 2, subagents, new Set()),
  ].join("\n");
}

/** agent_ids of subagents spawned directly within a thread. */
function childAgentIds(thread: Thread): string[] {
  const ids: string[] = [];
  for (const it of thread.items) {
    for (const b of it.blocks) {
      if (b.tool_use?.subagent) ids.push(b.tool_use.subagent.agent_id);
    }
  }
  return ids;
}

/** Fetch every subagent thread in the session (breadth-first, all depths). The
 * `getSubagent` fetcher is injected so this stays decoupled from the api module.
 * Missing/failed fetches are skipped (rendered as "transcript unavailable"). */
export async function fetchSubagentTree(
  sessionId: string,
  main: Thread,
  getSubagent: (sessionId: string, agentId: string) => Promise<Thread>,
): Promise<Map<string, Thread>> {
  const map = new Map<string, Thread>();
  let frontier = childAgentIds(main);
  while (frontier.length) {
    const todo = frontier.filter((a) => !map.has(a));
    const fetched = await Promise.all(
      todo.map(async (a) => {
        try {
          return [a, await getSubagent(sessionId, a)] as const;
        } catch {
          return null;
        }
      }),
    );
    const next: string[] = [];
    for (const r of fetched) {
      if (!r) continue;
      map.set(r[0], r[1]);
      next.push(...childAgentIds(r[1]));
    }
    frontier = next;
  }
  return map;
}

export function downloadBlob(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function downloadMarkdown(filename: string, content: string): void {
  downloadBlob(filename, content, "text/markdown;charset=utf-8");
}
