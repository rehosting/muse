import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { useNavigate } from "react-router-dom";
import type { InvestigationRef } from "../api/types";
import "../hljs-github-dark.css";

/** Renders an investigation's markdown body, turning links that point at a muse
 * session into IN-APP navigations (so a finding can cite its evidence inline and
 * the user jumps straight to that step). Recognized link hrefs:
 *   - `#<anchor_uuid>`            → resolved against the investigation's refs
 *   - `/sessions/<id>?focus=<a>`  → relative deep-link
 *   - an absolute same-origin muse URL to /sessions/...
 * Anything else renders as a normal external link. */
/** Pull (sessionId, anchor) out of a resolved internal /sessions/<id>?focus=<a> link. */
function parseInternal(internal: string): { sessionId: string; anchor: string | null } | null {
  const m = internal.match(/^\/sessions\/([^?]+)(?:\?focus=(.+))?$/);
  if (!m) return null;
  return { sessionId: decodeURIComponent(m[1]), anchor: m[2] ? decodeURIComponent(m[2]) : null };
}

function toInternal(href: string | undefined, refs: InvestigationRef[]): string | null {
  if (!href) return null;
  if (href.startsWith("#")) {
    const anchor = decodeURIComponent(href.slice(1));
    const ref = refs.find((r) => r.anchor_uuid === anchor);
    return ref ? `/sessions/${ref.session_id}?focus=${anchor}` : null;
  }
  if (href.startsWith("/sessions/")) return href;
  try {
    const u = new URL(href, window.location.origin);
    if (u.origin === window.location.origin && u.pathname.startsWith("/sessions/")) {
      return u.pathname + u.search;
    }
  } catch {
    /* not a URL */
  }
  return null;
}

function InvestigationBodyImpl({
  body,
  refs,
  onOpenRef,
}: {
  body: string;
  refs: InvestigationRef[];
  /** When set, internal citations open in the split view's session pane instead
   * of navigating away. */
  onOpenRef?: (sessionId: string, anchor: string | null) => void;
}) {
  const navigate = useNavigate();
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          a: ({ href, children }) => {
            const internal = toInternal(href, refs);
            if (internal) {
              return (
                <a
                  href={internal}
                  className="inv-cite"
                  onClick={(e) => {
                    e.preventDefault();
                    const parsed = onOpenRef && parseInternal(internal);
                    if (parsed) onOpenRef!(parsed.sessionId, parsed.anchor);
                    else navigate(internal);
                  }}
                >
                  {children}
                </a>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
        }}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}

export default memo(InvestigationBodyImpl);
