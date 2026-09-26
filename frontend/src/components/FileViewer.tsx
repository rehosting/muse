import { useCallback, useEffect, useState } from "react";
import { copyToClipboard } from "../util/shell";
import { langForPath } from "../util/highlight";
import { VIEW_FILE_EVENT } from "../util/openFile";
import { basename, isMarkdownPath, useFileContent } from "../hooks/useFileContent";
import Markdown from "./Markdown";
import CodeBlock from "./CodeBlock";

const isMarkdown = isMarkdownPath;

/** Global click-to-view file modal. Opened via the "muse:view-file" event (openFile(path)).
 * Reads the file's live on-disk bytes (paginated) and renders markdown with the app's
 * Markdown component (Raw toggle for source) or code with shiki. Esc / scrim-click close. */
export default function FileViewer() {
  const [path, setPath] = useState<string | null>(null);
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const { content, size, nextOffset, loading, error, loadMore } = useFileContent(path);

  // Open on the global event.
  useEffect(() => {
    const onOpen = (e: Event) => {
      const p = (e as CustomEvent<{ path?: string }>).detail?.path;
      if (p) setPath(p);
    };
    window.addEventListener(VIEW_FILE_EVENT, onOpen);
    return () => window.removeEventListener(VIEW_FILE_EVENT, onOpen);
  }, []);

  const close = useCallback(() => setPath(null), []);

  // Re-render fresh when a new path opens (source toggle/copy are per-file).
  useEffect(() => {
    setRaw(false);
    setCopied(false);
  }, [path]);

  // Esc closes.
  useEffect(() => {
    if (!path) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [path, close]);

  const onCopyPath = async () => {
    if (!path) return;
    const ok = await copyToClipboard(path);
    setCopied(ok);
    setTimeout(() => setCopied(false), 1400);
  };

  if (!path) return null;

  const md = isMarkdown(path);
  return (
    <>
      <div className="file-viewer-scrim" onClick={close} />
      <div className="file-viewer" role="dialog" aria-label={`File: ${path}`}>
        <div className="file-viewer-head">
          <span className="file-viewer-name" title={path}>
            {basename(path)}
          </span>
          <span className="file-viewer-path" title={path}>
            {path}
          </span>
          <div className="file-viewer-actions">
            {md && (
              <button
                className="file-viewer-btn"
                onClick={() => setRaw((r) => !r)}
                title={raw ? "Rendered markdown" : "View source"}
              >
                {raw ? "Rendered" : "Raw"}
              </button>
            )}
            <button className="file-viewer-btn" onClick={onCopyPath} title="Copy path">
              {copied ? "✓ copied" : "Copy path"}
            </button>
            <button className="file-viewer-btn" onClick={close} title="Close (Esc)">
              ✕
            </button>
          </div>
        </div>
        <div className="file-viewer-body">
          {error ? (
            <div className="file-viewer-error">{error}</div>
          ) : !content && loading ? (
            <div className="file-viewer-empty">Loading…</div>
          ) : md && !raw ? (
            <Markdown>{content}</Markdown>
          ) : (
            <CodeBlock code={content} lang={md ? "markdown" : langForPath(path)} />
          )}
          {nextOffset != null && !error && (
            <button className="file-viewer-more" onClick={loadMore} disabled={loading}>
              {loading ? "Loading…" : `Load more (${Math.round(nextOffset / 1024)} KB of ${Math.round(size / 1024)} KB)`}
            </button>
          )}
        </div>
      </div>
    </>
  );
}
