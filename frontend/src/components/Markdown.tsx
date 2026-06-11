import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "../hljs-github-dark.css";

/**
 * GitHub-flavored markdown with synchronous code highlighting (highlight.js).
 * Used heavily in the conversation, so it must be fast: highlight.js is
 * synchronous and light (no WASM), and we memoize on the source string so it
 * doesn't re-parse when the thread re-renders (selection, live ticks, etc.).
 * The detail view uses shiki (CodeBlock) for a smaller number of blocks.
 */
function MarkdownImpl({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownImpl);
