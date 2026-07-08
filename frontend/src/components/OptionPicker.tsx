import { useState } from "react";
import type { PendingOptions } from "../api/types";

const SOURCE_BADGE: Record<string, string> = {
  permission: "permission",
  tool_question: "question",
  none: "",
};

/**
 * Renders the choices a live session is presenting as big tappable chips —
 * uniformly across all sources (permission dialog, AskUserQuestion, ExitPlanMode).
 * A `free_text` option opens an inline composer that posts a typed reply instead
 * of a menu pick.
 */
export default function OptionPicker({
  pending,
  sending,
  onSelect,
}: {
  pending: PendingOptions;
  sending: boolean;
  onSelect: (optionId: string, freeText?: string) => void;
}) {
  const [typing, setTyping] = useState<string | null>(null); // option id in free-text mode
  const [freeText, setFreeText] = useState("");

  return (
    <div className="option-picker" role="group" aria-label="Pending options">
      <div className="option-picker-head">
        {SOURCE_BADGE[pending.source] && (
          <span className="option-badge">{SOURCE_BADGE[pending.source]}</span>
        )}
        <span className="option-prompt">{pending.prompt || "Choose an option"}</span>
        {pending.remaining_questions > 0 && (
          <span className="option-more">+{pending.remaining_questions} more after this</span>
        )}
      </div>
      <div className="option-chips">
        {pending.options.map((o) =>
          o.kind === "free_text" ? (
            typing === o.id ? (
              <div key={o.id} className="option-freetext">
                <textarea
                  className="option-freetext-input"
                  autoFocus
                  placeholder="Type your reply…"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  value={freeText}
                  onChange={(e) => setFreeText(e.target.value)}
                />
                <button
                  className="option-chip option-chip-send"
                  disabled={sending || !freeText.trim()}
                  onClick={() => onSelect(o.id, freeText.trim())}
                >
                  Send
                </button>
              </div>
            ) : (
              <button
                key={o.id}
                className="option-chip option-chip-other"
                disabled={sending}
                onClick={() => setTyping(o.id)}
              >
                {o.label}
              </button>
            )
          ) : (
            <button
              key={o.id}
              className="option-chip"
              disabled={sending}
              onClick={() => onSelect(o.id)}
            >
              <span className="option-chip-label">{o.label}</span>
              {o.description && <span className="option-chip-desc">{o.description}</span>}
            </button>
          ),
        )}
      </div>
    </div>
  );
}
