import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Note } from "../api/types";

const KIND_ICON: Record<string, string> = { note: "📝", next: "⏭", brief: "🧭" };

/** Worklog notes for this session: a thin bar with a quick-add input, expandable
 * to the full chronological list. Notes are muse-owned (lightweight running notes
 * about active work — "next:" entries are open loops that surface on the home
 * page). Anchored notes jump the view to their step via onFocus. */
export default function NotesPanel({
  sessionId,
  onFocus,
}: {
  sessionId: string;
  onFocus: (uuid: string) => void;
}) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    let ok = true;
    const load = () =>
      api
        .listNotes({ sessionId })
        .then((n) => ok && setNotes(n))
        .catch(() => ok && setNotes([]));
    load();
    // Refetch when something else lands a note (e.g. an AI summary job).
    window.addEventListener("muse:notes-refresh", load);
    return () => {
      ok = false;
      window.removeEventListener("muse:notes-refresh", load);
    };
  }, [sessionId]);

  const add = async () => {
    const text = draft.trim();
    if (!text) return;
    // "next: …" prefix marks an open loop (surfaces in the continue-working rail)
    const isNext = /^next\s*:/i.test(text);
    const note = await api.createNote({
      body: isNext ? text.replace(/^next\s*:\s*/i, "") : text,
      session_id: sessionId,
      kind: isNext ? "next" : "note",
    });
    setNotes((prev) => [note, ...prev]);
    setDraft("");
  };

  const remove = async (id: string) => {
    await api.deleteNote(id);
    setNotes((prev) => prev.filter((n) => n.id !== id));
  };

  return (
    <div className="notes-bar">
      <button
        className="notes-toggle"
        title={open ? "Collapse notes" : "Show notes"}
        onClick={() => setOpen((o) => !o)}
      >
        📝 Notes{notes.length > 0 ? ` (${notes.length})` : ""} {open ? "▾" : "▸"}
      </button>
      <input
        className="notes-quick-add"
        placeholder='Quick note… (prefix "next:" to mark an open loop)'
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") add();
        }}
      />
      {open && notes.length > 0 && (
        <ul className="notes-list">
          {notes.map((n) => (
            <li key={n.id} className={`note-row note-${n.kind}`}>
              <span className="note-kind" title={n.kind}>
                {KIND_ICON[n.kind] ?? "📝"}
              </span>
              <span className="note-body">{n.body}</span>
              <span className="note-meta">
                {n.author === "ai" && <span className="note-ai">ai</span>}
                {n.created_at && (
                  <span title={n.created_at}>
                    {new Date(n.created_at).toLocaleString()}
                  </span>
                )}
              </span>
              {n.anchor_uuid && (
                <button
                  className="backlink-ref"
                  title="jump to the noted step"
                  onClick={() => onFocus(n.anchor_uuid as string)}
                >
                  ↪ step
                </button>
              )}
              <button
                className="note-delete"
                title="delete note"
                onClick={() => remove(n.id)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
