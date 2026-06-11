import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SearchHit, SessionSummary } from "../api/types";
import { matchCommands, type Command } from "../util/commands";
import { copyToClipboard, resumeCommand } from "../util/shell";
import { toggleTheme } from "./ThemeToggle";
import { relativeTime } from "../util/format";

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

type Row =
  | { kind: "command"; cmd: Command }
  | { kind: "recent"; s: SessionSummary }
  | { kind: "hit"; h: SearchHit };

/** Global ⌘K palette: commands (navigation / theme / new note / new session)
 * plus full-text search across all sessions. `>` filters commands only.
 * On a session row: ↵ open · ⌘↵ copy resume command · ⌥↵ compare with current. */
export default function CommandPalette() {
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [recents, setRecents] = useState<SessionSummary[]>([]);
  const [meta, setMeta] = useState<{ indexed: number; available: boolean; loose: boolean }>({
    indexed: 0,
    available: true,
    loose: false,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [noteMode, setNoteMode] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
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
      // Status + recent sessions (both cheap: TTL-cached server side).
      api.search("").then((res) => setMeta({ indexed: res.indexed_sessions, available: res.available, loose: false })).catch(() => {});
      api.listSessions().then((ss) => setRecents(ss.slice(0, 8))).catch(() => {});
    } else {
      setQ("");
      setHits([]);
      setCursor(0);
      setError(null);
      setNoteMode(false);
      setFlash(null);
    }
  }, [open]);

  const isCommandQuery = q.startsWith(">");
  const searchQuery = isCommandQuery || noteMode ? "" : q.trim();

  // Debounced search with an 8s timeout so a slow backend surfaces an error
  // instead of looking dead.
  useEffect(() => {
    if (!searchQuery) {
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
        .search(searchQuery, 30, controller.signal)
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
  }, [searchQuery]);

  // The unified row list driving cursor/Enter.
  const rows: Row[] = useMemo(() => {
    if (noteMode) return [];
    if (isCommandQuery) {
      return matchCommands(q.slice(1).trim()).map((cmd) => ({ kind: "command", cmd }));
    }
    if (!q.trim()) {
      return [
        ...matchCommands("").map((cmd) => ({ kind: "command", cmd }) as Row),
        ...recents.map((s) => ({ kind: "recent", s }) as Row),
      ];
    }
    const cmds = matchCommands(q.trim()).slice(0, 3);
    return [
      ...cmds.map((cmd) => ({ kind: "command", cmd }) as Row),
      ...hits.map((h) => ({ kind: "hit", h }) as Row),
    ];
  }, [noteMode, isCommandQuery, q, recents, hits]);

  useEffect(() => {
    setCursor(0);
  }, [q, noteMode]);

  const currentSessionId = location.pathname.startsWith("/sessions/")
    ? location.pathname.split("/")[2]
    : null;

  const runCommand = useCallback(
    (cmd: Command) => {
      if (cmd.action === "theme") {
        toggleTheme();
        setFlash("Theme toggled");
        return; // keep the palette open — it's a toggle
      }
      if (cmd.action === "note") {
        setNoteMode(true);
        setQ("");
        requestAnimationFrame(() => inputRef.current?.focus());
        return;
      }
      setOpen(false);
      if (cmd.action === "launch") {
        window.dispatchEvent(new CustomEvent("muse:launch", { detail: {} }));
        return;
      }
      if (cmd.to) navigate(cmd.to);
    },
    [navigate],
  );

  const openSession = useCallback(
    (sessionId: string, focus: string | null, e?: { metaKey?: boolean; ctrlKey?: boolean; altKey?: boolean }, cwd?: string | null) => {
      if (e && (e.metaKey || e.ctrlKey)) {
        copyToClipboard(resumeCommand(cwd ?? null, sessionId));
        setFlash("Resume command copied");
        return;
      }
      if (e?.altKey && currentSessionId && currentSessionId !== sessionId) {
        setOpen(false);
        navigate(`/compare?a=${currentSessionId}&b=${sessionId}`);
        return;
      }
      setOpen(false);
      navigate(`/sessions/${sessionId}${focus ? `?focus=${focus}` : ""}`);
    },
    [navigate, currentSessionId],
  );

  const activate = useCallback(
    (row: Row, e?: { metaKey?: boolean; ctrlKey?: boolean; altKey?: boolean }) => {
      if (row.kind === "command") runCommand(row.cmd);
      else if (row.kind === "recent")
        openSession(row.s.session_id, null, e, row.s.project_cwd);
      else openSession(row.h.session_id, row.h.uuid, e, row.h.project_cwd);
    },
    [runCommand, openSession],
  );

  const saveNote = async () => {
    const body = q.trim();
    if (!body) return;
    const isNext = /^next\s*:/i.test(body);
    await api.createNote({
      body: isNext ? body.replace(/^next\s*:\s*/i, "") : body,
      kind: isNext ? "next" : "note",
      session_id: currentSessionId ?? undefined,
    });
    setFlash(currentSessionId ? "Note saved to this session" : "Note saved");
    setQ("");
    setNoteMode(false);
  };

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      if (noteMode) setNoteMode(false);
      else setOpen(false);
    } else if (noteMode && e.key === "Enter") {
      e.preventDefault();
      saveNote();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(rows.length - 1, c + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(0, c - 1));
    } else if (e.key === "Enter" && rows[cursor]) {
      e.preventDefault();
      activate(rows[cursor], e);
    }
  };

  let lastKind: string | null = null;
  const header = (kind: string) =>
    kind === "command" ? "Commands" : kind === "recent" ? "Recent sessions" : "Search results";

  return (
    <>
      <div className="cmdk-overlay" onClick={() => setOpen(false)} />
      <div className="cmdk" role="dialog" aria-label="Command palette">
        <input
          ref={inputRef}
          className="cmdk-input"
          placeholder={
            noteMode
              ? 'New note… (prefix "next:" to mark an open loop, Esc to cancel)'
              : "Search sessions… ( > for commands )"
          }
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="cmdk-results">
          {flash && <div className="cmdk-loose">{flash}</div>}
          {noteMode && (
            <div className="cmdk-empty">
              ↵ saves the note{currentSessionId ? " to the current session" : ""}.
            </div>
          )}
          {!noteMode && !meta.available && (
            <div className="cmdk-empty">Search unavailable (SQLite built without FTS5).</div>
          )}
          {!noteMode && meta.available && loading && hits.length === 0 && !!searchQuery && (
            <div className="cmdk-empty">Searching…</div>
          )}
          {!noteMode && meta.available && error && !loading && (
            <div className="cmdk-empty cmdk-error">{error}</div>
          )}
          {!noteMode && meta.available && searchQuery && !loading && !error && rows.length === 0 && (
            <div className="cmdk-empty">
              No matches. Filters: <code>project:</code> <code>role:</code>{" "}
              <code>provider:</code> <code>after:YYYY-MM-DD</code>
            </div>
          )}
          {!noteMode && meta.available && meta.loose && hits.length > 0 && (
            <div className="cmdk-loose">
              No exact match for all terms — showing any-term matches.
            </div>
          )}
          {rows.map((row, i) => {
            const head = row.kind !== lastKind ? header(row.kind) : null;
            lastKind = row.kind;
            return (
              <div key={i}>
                {head && <div className="cmdk-section">{head}</div>}
                <div
                  className={`cmdk-hit${i === cursor ? " active" : ""}`}
                  onMouseEnter={() => setCursor(i)}
                  onClick={(e) => activate(row, e)}
                >
                  {row.kind === "command" && (
                    <div className="cmdk-hit-head">
                      <span className="cmdk-cmd-icon">▸</span>
                      <span className="cmdk-hit-title">{row.cmd.label}</span>
                      {row.cmd.hint && <span className="cmdk-hit-time">{row.cmd.hint}</span>}
                    </div>
                  )}
                  {row.kind === "recent" && (
                    <div className="cmdk-hit-head">
                      <span className="cmdk-hit-title">{row.s.title}</span>
                      <span className="cmdk-hit-time">{relativeTime(row.s.mtime)}</span>
                    </div>
                  )}
                  {row.kind === "hit" && (
                    <>
                      <div className="cmdk-hit-head">
                        <span className="cmdk-hit-title">{row.h.title}</span>
                        <span className={`cmdk-hit-role role-${row.h.role ?? "x"}`}>
                          {row.h.role}
                        </span>
                        {row.h.timestamp && (
                          <span className="cmdk-hit-time">
                            {new Date(row.h.timestamp).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                      <div className="cmdk-hit-snip">
                        <Snippet text={row.h.snippet} />
                      </div>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span>↑↓ · ↵ open · ⌘↵ copy resume · ⌥↵ compare · esc</span>
          {meta.available && <span>{meta.indexed} sessions indexed</span>}
        </div>
      </div>
    </>
  );
}
