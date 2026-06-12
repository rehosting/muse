import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Journal, Note } from "../api/types";
import { relativeTime } from "../util/format";
import AiActionButton from "../components/AiActionButton";

const KIND_ICON: Record<string, string> = { note: "📝", next: "⏭", brief: "🧭" };

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

function shiftDay(day: string, delta: number): string {
  const d = new Date(`${day}T12:00:00`);
  d.setDate(d.getDate() + delta);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

/** One day of work: global quick-add at the top, then notes grouped under the
 * sessions they belong to (sessions active that day are listed even when
 * note-less, so the journal doubles as a daily activity log). */
export default function JournalPage() {
  const [day, setDay] = useState(todayStr());
  const [journal, setJournal] = useState<Journal | null>(null);
  const [draft, setDraft] = useState("");

  const load = useCallback(
    () => api.getJournal(day).then(setJournal).catch(() => setJournal(null)),
    [day],
  );
  useEffect(() => {
    load();
  }, [load]);

  const add = async () => {
    const text = draft.trim();
    if (!text) return;
    const isNext = /^next\s*:/i.test(text);
    await api.createNote({
      body: isNext ? text.replace(/^next\s*:\s*/i, "") : text,
      kind: isNext ? "next" : "note",
    });
    setDraft("");
    load();
  };

  const remove = async (id: string) => {
    await api.deleteNote(id);
    load();
  };

  const { globalNotes, bySession } = useMemo(() => {
    const globalNotes: Note[] = [];
    const bySession = new Map<string, Note[]>();
    for (const n of journal?.notes ?? []) {
      if (!n.session_id) globalNotes.push(n);
      else {
        const arr = bySession.get(n.session_id) ?? [];
        arr.push(n);
        bySession.set(n.session_id, arr);
      }
    }
    return { globalNotes, bySession };
  }, [journal]);

  const sessionTitle = (sid: string) =>
    journal?.sessions.find((s) => s.session_id === sid)?.title ?? sid.slice(0, 8);

  // Sessions with notes first, then the rest of the day's activity.
  const noteSids = [...bySession.keys()];
  const quietSessions = (journal?.sessions ?? []).filter(
    (s) => !bySession.has(s.session_id),
  );

  const noteRow = (n: Note) => (
    <li key={n.id} className={`note-row note-${n.kind}`}>
      <span className="note-kind" title={n.kind}>
        {KIND_ICON[n.kind] ?? "📝"}
      </span>
      <span className="note-body">{n.body}</span>
      <span className="note-meta">
        {n.author === "ai" && <span className="note-ai">ai</span>}
        {n.created_at && (
          <span title={n.created_at}>
            {new Date(n.created_at).toLocaleTimeString()}
          </span>
        )}
      </span>
      <button className="note-delete" title="delete note" onClick={() => remove(n.id)}>
        ✕
      </button>
    </li>
  );

  return (
    <div className="list-wrap journal-wrap">
      <div className="journal-head">
        <h2 className="list-heading">Journal</h2>
        <button className="action-btn" onClick={() => setDay(shiftDay(day, -1))}>
          ←
        </button>
        <input
          type="date"
          className="journal-day"
          value={day}
          onChange={(e) => e.target.value && setDay(e.target.value)}
        />
        <button
          className="action-btn"
          onClick={() => setDay(shiftDay(day, 1))}
          disabled={day >= todayStr()}
        >
          →
        </button>
        {day !== todayStr() && (
          <button className="action-btn" onClick={() => setDay(todayStr())}>
            Today
          </button>
        )}
        <span className="journal-spacer" />
        <AiActionButton
          label="✦ Generate digest"
          title={`AI 'what happened on ${day}' journal entry (headless claude; lands as a note)`}
          enqueue={() => api.generateDailyDigest(day)}
          onDone={load}
        />
      </div>

      <input
        className="notes-quick-add journal-add"
        placeholder='What are you working on? (prefix "next:" to mark an open loop)'
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") add();
        }}
      />

      {globalNotes.length > 0 && (
        <section className="journal-section">
          <h3 className="journal-session-title">General</h3>
          <ul className="notes-list">{globalNotes.map(noteRow)}</ul>
        </section>
      )}

      {noteSids.map((sid) => (
        <section className="journal-section" key={sid}>
          <h3 className="journal-session-title">
            <Link to={`/sessions/${sid}`}>{sessionTitle(sid)}</Link>
          </h3>
          <ul className="notes-list">{bySession.get(sid)!.map(noteRow)}</ul>
        </section>
      ))}

      {quietSessions.length > 0 && (
        <section className="journal-section">
          <h3 className="journal-session-title dim">Also active this day</h3>
          <ul className="journal-quiet">
            {quietSessions.map((s) => (
              <li key={s.session_id}>
                <Link to={`/sessions/${s.session_id}`}>{s.title}</Link>
                <span className="note-meta"> · {relativeTime(s.mtime)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!journal && <div className="empty">Loading…</div>}
      {journal && journal.notes.length === 0 && journal.sessions.length === 0 && (
        <div className="empty">Nothing recorded on {day}.</div>
      )}
    </div>
  );
}
