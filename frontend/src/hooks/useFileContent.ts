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

const WRAP_KEY = "fileWrap";
// Same breakpoint the mobile stylesheet uses.
const NARROW_PX = 700;

/**
 * Soft-wrap preference for the file viewers, shared by the modal and the deep-link
 * page and remembered across files and reloads.
 *
 * The default depends on the viewport rather than being a flat false: on a phone a
 * long line means horizontally scrolling a modal, which is miserable, while on a wide
 * screen the column alignment of code is usually worth keeping. Once the user picks a
 * side, that choice wins everywhere — a stored preference is never second-guessed by
 * the viewport.
 */
export function useWrapLines(): [boolean, () => void] {
  const [wrap, setWrap] = useState(() => {
    const saved = typeof localStorage !== "undefined" ? localStorage.getItem(WRAP_KEY) : null;
    if (saved === "1") return true;
    if (saved === "0") return false;
    return typeof window !== "undefined" && window.innerWidth <= NARROW_PX;
  });

  const toggle = useCallback(() => {
    setWrap((w) => {
      try {
        localStorage.setItem(WRAP_KEY, w ? "0" : "1");
      } catch {
        // private-mode / quota — the toggle still works for this session
      }
      return !w;
    });
  }, []);

  return [wrap, toggle];
}
