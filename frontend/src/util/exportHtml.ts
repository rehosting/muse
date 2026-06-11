import type { Thread, ThreadItem } from "../api/types";
import { summarize } from "../components/renderers";
import { shortModel } from "./format";
import { NOOP_REDACTOR, type Redactor } from "./redact";

/** Structural clone of exportMarkdown.ts that emits ONE self-contained HTML
 * file styled like the terminal conversation (dark, monospace, ⏺/⎿ glyphs),
 * with <details> for thinking blocks and subagent transcripts. No framework,
 * no hljs — plain <pre> keeps the file small and dependency-free. */

const STYLE = `
:root { color-scheme: dark; }
body { background:#1a1915; color:#e8e6dc; margin:0; padding:32px 24px 80px;
  font:13px/1.55 ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace; }
.wrap { max-width: 980px; margin: 0 auto; }
.banner { border:1px solid #3a3733; border-radius:8px; padding:10px 14px;
  margin-bottom:18px; max-width:560px; }
.banner .star { color:#d97757; margin-right:8px; }
.banner .l1 { font-weight:700; }
.banner .dim, .dim { color:#999184; }
.user { margin:14px 0 10px; color:#999184; white-space:pre-wrap; word-break:break-word; }
.user .prompt { user-select:none; }
.a-text { margin:7px 0; white-space:pre-wrap; word-break:break-word; }
.bullet { margin-right:8px; user-select:none; }
.b-ok { color:#6fae7d; } .b-err { color:#e5727a; }
.tool { margin:7px 0; }
.tool .name { font-weight:700; }
.tool .arg { color:#999184; }
.result { margin:2px 0 2px 14px; display:flex; gap:8px; color:#999184; }
.result pre { margin:0; white-space:pre-wrap; word-break:break-word; }
details { margin:7px 0; }
summary { color:#999184; font-style:italic; cursor:pointer; }
.thinking-body { padding-left:18px; color:#999184; font-style:italic; white-space:pre-wrap; }
.subagent { border-left:2px solid #3a3733; padding-left:14px; margin:6px 0; }
.pill { border:1px solid #3a3733; border-radius:5px; font-size:10px; padding:0 5px;
  color:#d9a066; margin-left:6px; }
.sys { color:#999184; white-space:pre-wrap; margin:8px 0; }
.footer { margin-top:40px; color:#999184; font-size:11px; border-top:1px solid #3a3733;
  padding-top:10px; }
`;

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toolsToHtml(
  item: ThreadItem,
  subagents: Map<string, Thread>,
  seen: Set<string>,
  r: Redactor,
): string[] {
  const out: string[] = [];
  for (const b of item.blocks) {
    if (b.kind !== "tool_use" || !b.tool_use) continue;
    const t = b.tool_use;
    const status = t.result?.is_error ? "b-err" : "b-ok";
    out.push(
      `<div class="tool"><span class="bullet ${status}">⏺</span>` +
        `<span class="name">${escapeHtml(t.name)}</span>` +
        `<span class="arg">(${escapeHtml(r.redact(summarize(t.name, t.input)))})</span>` +
        (t.subagent ? `<span class="pill">${escapeHtml(t.subagent.agent_type)}</span>` : "") +
        `</div>`,
    );
    const res = t.result;
    if (res) {
      const text = res.content ?? res.preview ?? "";
      const lines = text.split("\n");
      const shown = lines.slice(0, 6).join("\n");
      const extra = lines.length - 6;
      out.push(
        `<div class="result"><span>⎿</span><pre>${escapeHtml(r.redact(shown))}` +
          (extra > 0 ? `\n<span class="dim">… +${extra} lines</span>` : "") +
          (res.truncated ? `\n<span class="dim">… (truncated)</span>` : "") +
          `</pre></div>`,
      );
    }
    if (t.subagent) {
      const sub = subagents.get(t.subagent.agent_id);
      if (sub && !seen.has(t.subagent.agent_id)) {
        const nseen = new Set(seen).add(t.subagent.agent_id);
        out.push(
          `<details><summary>🧵 Subagent: ${escapeHtml(t.subagent.agent_type)} — ` +
            `${escapeHtml(r.redact(t.subagent.description))} (${sub.items.length} msgs)</summary>` +
            `<div class="subagent">${renderThread(sub, subagents, nseen, r)}</div></details>`,
        );
      }
    }
  }
  return out;
}

function renderThread(
  thread: Thread,
  subagents: Map<string, Thread>,
  seen: Set<string>,
  r: Redactor,
): string {
  const parts: string[] = [];
  for (const item of thread.items) {
    if (item.role === "user") {
      if (!item.text) continue;
      parts.push(
        `<div class="user"><span class="prompt">&gt; </span>${escapeHtml(r.redact(item.text))}</div>`,
      );
    } else if (item.role === "system") {
      if (item.text) parts.push(`<div class="sys">${escapeHtml(r.redact(item.text))}</div>`);
    } else {
      for (const b of item.blocks) {
        if (b.kind === "text" && b.text) {
          parts.push(
            `<div class="a-text"><span class="bullet">⏺</span>${escapeHtml(r.redact(b.text))}</div>`,
          );
        } else if (b.kind === "thinking" && b.text) {
          parts.push(
            `<details><summary>✻ Thinking…</summary>` +
              `<div class="thinking-body">${escapeHtml(r.redact(b.text))}</div></details>`,
          );
        }
      }
      parts.push(...toolsToHtml(item, subagents, seen, r));
    }
  }
  return parts.join("\n");
}

export function sessionToHtml(
  main: Thread,
  subagents: Map<string, Thread>,
  redactor: Redactor = NOOP_REDACTOR,
): string {
  const r = redactor;
  const model = main.model ? `${shortModel(main.model)}` : "";
  const body = renderThread(main, subagents, new Set(), r);
  const redacted = r.count() > 0;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(r.redact(main.title))}</title>
<style>${STYLE}</style>
</head>
<body><div class="wrap">
<div class="banner">
  <div class="l1"><span class="star">✻</span>${escapeHtml(r.redact(main.title))}</div>
  ${model ? `<div class="dim">${escapeHtml(model)}</div>` : ""}
  ${main.project_cwd ? `<div class="dim">cwd: ${escapeHtml(r.redact(main.project_cwd))}</div>` : ""}
  ${subagents.size ? `<div class="dim">${subagents.size} subagent transcript(s) inlined</div>` : ""}
</div>
${body}
<div class="footer">Exported from muse · session ${escapeHtml(main.session_id)}${
    redacted
      ? " · best-effort redaction applied (pattern-based — review before sharing)"
      : ""
  }</div>
</div></body>
</html>`;
}
