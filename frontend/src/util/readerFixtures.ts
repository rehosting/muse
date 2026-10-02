import type { ContentBlock, Thread, ThreadItem } from "../api/types";

/** Minimal ThreadItem for reader tests: `results` controls how many tool_use
 * blocks carry a result (the thing that mutates on an already-shipped item). */
export function makeItem(uuid: string, results = 0, tools = 0): ThreadItem {
  const nTools = Math.max(results, tools);
  // Renderers draw from blocks (item.text is a convenience concatenation), so
  // the visible text must be a real text block.
  const textBlock: ContentBlock = {
    kind: "text" as ContentBlock["kind"],
    text: `text of ${uuid}`,
    tool_use: null,
  };
  const blocks: ContentBlock[] = Array.from({ length: nTools }, (_, i) => ({
    kind: "tool_use" as ContentBlock["kind"],
    text: null,
    tool_use: {
      id: `${uuid}-t${i}`,
      name: "Bash",
      input: {},
      caller: null,
      result:
        i < results
          ? ({ content: "ok", is_error: false } as unknown as NonNullable<
              NonNullable<ContentBlock["tool_use"]>["result"]
            >)
          : null,
      subagent: null,
    },
  }));
  return {
    uuid,
    parent_uuid: null,
    role: "assistant",
    type: "assistant",
    timestamp: null,
    blocks: [textBlock, ...blocks],
    text: `text of ${uuid}`,
    usage: null,
    model: null,
    is_sidechain: false,
    level: null,
  };
}

/** Assistant item carrying ONLY tool calls (no text block) — the shape real
 * transcripts use for almost every step of a working turn. `results` of the
 * `tools` calls carry a result; `errors` of those results are failures. */
export function makeToolOnlyItem(
  uuid: string,
  tools = 1,
  results = 0,
  opts: { errors?: number; thinking?: boolean; name?: string } = {},
): ThreadItem {
  const { errors = 0, thinking = false, name = "Bash" } = opts;
  const blocks: ContentBlock[] = Array.from({ length: tools }, (_, i) => ({
    kind: "tool_use" as ContentBlock["kind"],
    text: null,
    tool_use: {
      id: `${uuid}-t${i}`,
      name,
      input: {},
      caller: null,
      result:
        i < results
          ? ({ content: "ok", is_error: i < errors, truncated: false } as unknown as NonNullable<
              NonNullable<ContentBlock["tool_use"]>["result"]
            >)
          : null,
      subagent: null,
    },
  }));
  if (thinking) {
    blocks.unshift({ kind: "thinking" as ContentBlock["kind"], text: "hmm", tool_use: null });
  }
  return { ...makeItem(uuid), text: null, blocks };
}

/** User item as tool_result carrier / harness wrapper — renders nothing. */
export function makeHiddenUserItem(uuid: string, raw: string | null = null): ThreadItem {
  return { ...makeItem(uuid), role: "user", type: "user", text: raw, blocks: [] };
}

/** A visible user message. */
export function makeUserItem(uuid: string, text = "hey"): ThreadItem {
  return {
    ...makeItem(uuid),
    role: "user",
    type: "user",
    text,
    blocks: [{ kind: "text" as ContentBlock["kind"], text, tool_use: null }],
  };
}

/** A thread window as the server ships it: items covering
 * [start, start+items.length) of a `total`-item thread. */
export function makeWindow(items: ThreadItem[], start: number, total: number): Thread {
  return {
    session_id: "sess",
    provider: "claude",
    project_cwd: "/proj",
    version: null,
    title: "t",
    title_source: "summary",
    model: null,
    context_window: null,
    items,
    usage_total: {
      input_tokens: 0,
      output_tokens: 0,
      cache_creation_input_tokens: 0,
      cache_read_input_tokens: 0,
    } as Thread["usage_total"],
    agent_id: null,
    agent_type: null,
    total_items: total,
    window_start: start,
    // Thread has viewer-only fields tests never touch; the double cast keeps the
    // fixture minimal without weakening what the tests do assert on.
  } as unknown as Thread;
}

/** items for full-thread indexes [from, to) — uuids are "m<i>" so tests can
 * assert exact coverage. */
export function makeItems(from: number, to: number): ThreadItem[] {
  return Array.from({ length: to - from }, (_, i) => makeItem(`m${from + i}`));
}
