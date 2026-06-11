import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SearchHit } from "../api/types";

/** Render an FTS snippet, turning the \x02/\x03 marker pairs into <mark>. */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/\x02(.*?)\x03/);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>,
      )}
    </>
  );
}

/** Global Cmd/Ctrl-K full-text search across all sessions. Mounted once in App. */
export default function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [meta, setMeta] = useState<{ indexed: number; available: boolean; loose: boolean }>({
    indexed: 0,
    available: true,
    loose: false,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const reqId = useRef(0);

  // Open/close hotkeys (Cmd/Ctrl-K toggles; also a custom event from the navbar).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("muse:search", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("muse:search", onOpen);
    };
  }, []);

  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => inputRef.current?.focus());
      // Show the real indexed count immediately (empty query = cheap status).
      api.search("").then((res) => setMeta({ indexed: res.indexed_sessions, available: res.available, loose: false })).catch(() => {});
    } else {
      setQ("");
      setHits([]);
      setCursor(0);
      setError(null);
    }
  }, [open]);

  // Debounced search with an 8s timeout so a slow backend surfaces an error
  // instead of looking dead.
  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setHits([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    const id = ++reqId.current;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    const t = setTimeout(() => {
      api
        .search(query, 30, controller.signal)
        .then((res) => {
          if (id !== reqId.current) return; // a newer query superseded this one
          setHits(res.hits);
          setMeta({ indexed: res.indexed_sessions, available: res.available, loose: res.loose });
          setCursor(0);
        })
        .catch((e) => {
          if (id !== reqId.current) return;
          setHits([]);
          setError(controller.signal.aborted ? "Search timed out — the server may be busy." : String(e));
        })
        .finally(() => {
          if (id === reqId.current) setLoading(false);
          clearTimeout(timeout);
        });
    }, 180);
    return () => {
      clearTimeout(t);
      clearTimeout(timeout);
    };
  }, [q]);

  const go = useCallback(
    (hit: SearchHit) => {
      setOpen(false);
      const focus = hit.uuid ? `?focus=${hit.uuid}` : "";
      navigate(`/sessions/${hit.session_id}${focus}`);
    },
    [navigate],
  );

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(hits.length - 1, c + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(0, c - 1));
    } else if (e.key === "Enter" && hits[cursor]) {
      e.preventDefault();
      go(hits[cursor]);
    }
  };

  return (
    <>
      <div className="cmdk-overlay" onClick={() => setOpen(false)} />
      <div className="cmdk" role="dialog" aria-label="Search all sessions">
        <input
          ref={inputRef}
          className="cmdk-input"
          placeholder="Search all sessions…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="cmdk-results">
          {!meta.available && (
            <div className="cmdk-empty">Search unavailable (SQLite built without FTS5).</div>
          )}
          {meta.available && loading && hits.length === 0 && (
            <div className="cmdk-empty">Searching…</div>
          )}
          {meta.available && error && !loading && (
            <div className="cmdk-empty cmdk-error">{error}</div>
          )}
          {meta.available && q.trim() && !loading && !error && hits.length === 0 && (
            <div className="cmdk-empty">
              No matches. Filters: <code>project:</code> <code>role:</code>{" "}
              <code>provider:</code> <code>after:YYYY-MM-DD</code>
            </div>
          )}
          {meta.available && meta.loose && hits.length > 0 && (
            <div className="cmdk-loose">
              No exact match for all terms — showing any-term matches.
            </div>
          )}
          {hits.map((h, i) => (
            <div
              key={`${h.session_id}:${h.uuid}:${i}`}
              className={`cmdk-hit${i === cursor ? " active" : ""}`}
              onMouseEnter={() => setCursor(i)}
              onClick={() => go(h)}
            >
              <div className="cmdk-hit-head">
                <span className="cmdk-hit-title">{h.title}</span>
                <span className={`cmdk-hit-role role-${h.role ?? "x"}`}>{h.role}</span>
                {h.timestamp && (
                  <span className="cmdk-hit-time">
                    {new Date(h.timestamp).toLocaleDateString()}
                  </span>
                )}
              </div>
              <div className="cmdk-hit-snip">
                <Snippet text={h.snippet} />
              </div>
            </div>
          ))}
        </div>
        <div className="cmdk-foot">
          <span>↑↓ navigate · ↵ open · esc close</span>
          {meta.available && <span>{meta.indexed} sessions indexed</span>}
        </div>
      </div>
    </>
  );
}
