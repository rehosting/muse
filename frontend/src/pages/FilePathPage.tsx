import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import CodeBlock from "../components/CodeBlock";
import Markdown from "../components/Markdown";
import { basename, isMarkdownPath, useFileContent } from "../hooks/useFileContent";
import { copyToClipboard } from "../util/shell";
import { langForPath } from "../util/highlight";

/**
 * Catch-all route: treat an unmatched URL path as an absolute file path and read it.
 *
 * Pasting `https://<host>/home/luke/.../plan.md` is the obvious way to share a file
 * from this machine, and it used to hit react-router's default error page. Markdown
 * renders through the app's Markdown component, so diagrams and GFM work the same as
 * in the click-to-view modal — this is a full page rather than that modal because a
 * deep link is usually opened to *read* something long.
 *
 * The backend decides what may be read (indexed project dirs + ~/.claude; anything
 * else is 403), so this does no path policing of its own beyond shape.
 */
export default function FilePathPage() {
  const location = useLocation();
  // The browser percent-encodes spaces etc.; the API wants the real path.
  const path = decodeURIComponent(location.pathname);
  const looksLikeFile = path.startsWith("/") && /\.[a-z0-9]{1,12}$/i.test(path);
  const { content, size, nextOffset, loading, error, loadMore } = useFileContent(
    looksLikeFile ? path : null,
  );
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!looksLikeFile) {
    return (
      <div className="list-wrap filepath-page">
        <h2 className="list-heading">Not found</h2>
        <p className="filepath-hint">
          <code>{path}</code> isn’t a muse page. To read a file, use its absolute path —
          e.g. <code>/home/you/project/notes.md</code> — or browse{" "}
          <Link to="/files">Files</Link>.
        </p>
      </div>
    );
  }

  const md = isMarkdownPath(path);
  return (
    <div className="list-wrap filepath-page">
      <div className="filepath-head">
        <div className="filepath-titles">
          <span className="filepath-name">{basename(path)}</span>
          <span className="filepath-full" title={path}>
            {path}
          </span>
        </div>
        <div className="filepath-actions">
          {md && (
            <button className="file-viewer-btn" onClick={() => setRaw((r) => !r)}>
              {raw ? "Rendered" : "Raw"}
            </button>
          )}
          <button
            className="file-viewer-btn"
            onClick={async () => {
              setCopied(await copyToClipboard(path));
              setTimeout(() => setCopied(false), 1400);
            }}
          >
            {copied ? "✓ copied" : "Copy path"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="filepath-error">
          {error}
          <div className="filepath-hint">
            muse only reads files under its indexed project directories and{" "}
            <code>~/.claude</code>. Anything else is refused by the server.
          </div>
        </div>
      ) : !content && loading ? (
        <div className="empty">Loading…</div>
      ) : md && !raw ? (
        <Markdown>{content}</Markdown>
      ) : (
        <CodeBlock code={content} lang={md ? "markdown" : langForPath(path)} />
      )}

      {nextOffset != null && !error && (
        <button className="file-viewer-more" onClick={loadMore} disabled={loading}>
          {loading
            ? "Loading…"
            : `Load more (${Math.round(nextOffset / 1024)} KB of ${Math.round(size / 1024)} KB)`}
        </button>
      )}
    </div>
  );
}
