import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

export const isMarkdownPath = (path: string) => /\.(md|markdown|mdx)$/i.test(path);
export const basename = (path: string) => path.split("/").pop() || path;

/**
 * Live on-disk bytes of one file, paginated.
 *
 * Shared by the click-to-view modal (FileViewer) and the deep-link route
 * (FilePathPage) so the two can't drift on fetching, pagination or error text —
 * they differ only in chrome.
 */
export function useFileContent(path: string | null) {
  const [content, setContent] = useState("");
  const [size, setSize] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    setContent("");
    setNextOffset(null);
    setError(null);
    setLoading(true);
    let alive = true;
    (async () => {
      try {
        const res = await api.readFile(path);
        if (!alive) return;
        setContent(res.content);
        setSize(res.size);
        setNextOffset(res.next_offset);
      } catch (e) {
        if (alive) setError(String(e instanceof Error ? e.message : e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [path]);

  const loadMore = useCallback(() => {
    if (!path || nextOffset == null || loading) return;
    setLoading(true);
    api
      .readFile(path, nextOffset)
      .then((res) => {
        setContent((c) => c + res.content);
        setNextOffset(res.next_offset);
      })
      .catch((e) => setError(String(e instanceof Error ? e.message : e)))
      .finally(() => setLoading(false));
  }, [path, nextOffset, loading]);

  return { content, size, nextOffset, loading, error, loadMore };
}
