import { useState } from "react";

/**
 * Per-message bookmark with an optional note. `note === undefined` means the
 * message isn't bookmarked yet.
 */
export default function BookmarkControl({
  note,
  onSave,
  onRemove,
}: {
  note: string | undefined;
  onSave: (note: string) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const startEdit = () => {
    setDraft(note ?? "");
    setEditing(true);
  };
  const save = () => {
    onSave(draft.trim());
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="bm bm-editing">
        <textarea
          className="bm-input"
          autoFocus
          placeholder="Add a note…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
            if (e.key === "Escape") setEditing(false);
          }}
        />
        <div className="bm-actions">
          <button className="bm-btn" onClick={save}>
            Save
          </button>
          <button className="bm-btn" onClick={() => setEditing(false)}>
            Cancel
          </button>
          {note !== undefined && (
            <button
              className="bm-btn bm-remove"
              onClick={() => {
                onRemove();
                setEditing(false);
              }}
            >
              Remove
            </button>
          )}
        </div>
      </div>
    );
  }

  if (note === undefined) {
    return (
      <button className="bm-add" title="Bookmark this message" onClick={startEdit}>
        🔖
      </button>
    );
  }

  return (
    <div className="bm bm-saved" onClick={startEdit} title="Edit note">
      <span className="bm-icon">🔖</span>
      <span className="bm-note">{note || "(no note)"}</span>
    </div>
  );
}
