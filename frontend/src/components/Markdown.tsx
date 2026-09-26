import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import MermaidBlock from "./MermaidBlock";
import "../hljs-github-dark.css";

/**
 * GitHub-flavored markdown with synchronous code highlighting (highlight.js).
 * Used heavily in the conversation, so it must be fast: highlight.js is
 * synchronous and light (no WASM), and we memoize on the source string so it
 * doesn't re-parse when the thread re-renders (selection, live ticks, etc.).
 * The detail view uses shiki (CodeBlock) for a smaller number of blocks.
 *
 * ```mermaid fences render as diagrams (MermaidBlock, which loads mermaid lazily);
 * every other fence stays highlighted source.
 */
function MarkdownImpl({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          // plainText: mermaid source must reach the code override as a STRING.
          // Highlighting would tokenize it into elements and String(children)
          // would then stringify React nodes instead of the diagram source.
          [rehypeHighlight, { detect: true, ignoreMissing: true, plainText: ["mermaid"] }],
        ]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
          code: ({ className, children, ...props }) => {
            // react-markdown gives fenced blocks a `language-x` class; inline code
            // has none. Only a fenced ```mermaid block becomes a diagram.
            const lang = /language-(\w+)/.exec(className || "")?.[1];
            if (lang === "mermaid") {
              return <MermaidBlock code={String(children).replace(/\n$/, "")} />;
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownImpl);
