import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Note } from "../api/types";
import { copyToClipboard } from "../util/shell";

interface LaunchDetail {
  sourceSessionId?: string;
  cwd?: string;
}

/** "New session" modal: pick a project, write a seed prompt, optionally attach
 * a context pack rendered from a prior session (brief / notes / files). Opens
 * via the `muse:launch` window event (same pattern as the ⌘K palette). Launches
 * in a new tmux window when available; always offers the copyable command. */
export default function LaunchModal() {
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState<string[]>([]);
  const [cwd, setCwd] = useState("");
  const [prompt, setPrompt] = useState("");
  const [sourceSession, setSourceSession] = useState<string | null>(null);
  const [includeBrief, setIncludeBrief] = useState(true);
  const [includeFiles, setIncludeFiles] = useState(true);
  const [includeNotes, setIncludeNotes] = useState(true);
  const [notes, setNotes] = useState<Note[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [command, setCommand] = useState<string | null>(null);

  useEffect(() => {
    const onLaunch = (e: Event) => {
      const detail = (e as CustomEvent<LaunchDetail>).detail ?? {};
      setSourceSession(detail.sourceSessionId ?? null);
      if (detail.cwd) setCwd(detail.cwd);
      setResult(null);
      setCommand(null);
      setOpen(true);
      api.getLaunchTargets().then((t) => {
        setTargets(t);
        setCwd((c) => c || detail.cwd || t[0] || "");
      }).catch(() => {});
    };
    window.addEventListener("muse:launch", onLaunch);
    return () => window.removeEventListener("muse:launch", onLaunch);
  }, []);

  useEffect(() => {
    if (!open || !sourceSession) {
      setNotes([]);
      return;
    }
    api.listNotes({ sessionId: sourceSession }).then(setNotes).catch(() => setNotes([]));
  }, [open, sourceSession]);

  if (!open) return null;

  const buildPackId = async (): Promise<string | null> => {
    if (!sourceSession || (!includeBrief && !includeFiles && !includeNotes)) return null;
    const pack = await api.createPack({
      source_session_id: sourceSession,
      include_brief: includeBrief,
      include_files: includeFiles,
      note_ids: includeNotes ? notes.map((n) => n.id) : [],
    });
    return pack.id;
  };

  const launch = async (copyOnly: boolean) => {
    if (!cwd.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const packId = await buildPackId();
      const res = await api.launchSession({
        cwd: cwd.trim(),
        prompt,
        pack_id: packId,
      });
      setCommand(res.command);
      if (copyOnly || !res.ok) {
        await copyToClipboard(res.command);
        setResult(
          res.ok || copyOnly
            ? "Command copied to clipboard — paste it in a terminal."
            : `tmux launch failed (${res.error}) — command copied instead.`,
        );
        if (!copyOnly && !res.ok) await copyToClipboard(res.command);
      } else {
        setResult(`Launched in tmux pane ${res.pane_id}.`);
      }
    } catch (e) {
      setResult(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="menu-overlay" onClick={() => setOpen(false)} />
      <div className="launch-modal">
        <h3 className="launch-title">✻ New Claude Code session</h3>

        <label className="launch-label">Project</label>
        <input
          className="notes-quick-add launch-field"
          list="launch-targets"
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          placeholder="/path/to/project"
        />
        <datalist id="launch-targets">
          {targets.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>

        <label className="launch-label">Seed prompt</label>
        <textarea
          className="notes-quick-add launch-field launch-prompt"
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="What should the new session do?"
        />

        {sourceSession && (
          <div className="launch-pack">
            <span className="launch-label">
              Attach context from the previous session
            </span>
            <label>
              <input type="checkbox" checked={includeBrief}
                     onChange={(e) => setIncludeBrief(e.target.checked)} />{" "}
              Where it left off (brief)
            </label>
            <label>
              <input type="checkbox" checked={includeNotes}
                     onChange={(e) => setIncludeNotes(e.target.checked)} />{" "}
              Worklog notes ({notes.length})
            </label>
            <label>
              <input type="checkbox" checked={includeFiles}
                     onChange={(e) => setIncludeFiles(e.target.checked)} />{" "}
              Files it touched
            </label>
            <p className="stat-note launch-note">
              Rendered to <code>~/.muse/packs/…md</code>; the seed prompt tells the new
              session to read it. Your project directory is never written to.
            </p>
          </div>
        )}

        {result && <div className="launch-result">{result}</div>}
        {command && (
          <pre className="launch-command" title="The exact command">{command}</pre>
        )}

        <div className="launch-actions">
          <button className="action-btn" onClick={() => setOpen(false)}>
            Close
          </button>
          <span className="loop-spacer" />
          <button className="action-btn" disabled={busy || !cwd.trim()} onClick={() => launch(true)}>
            ⧉ Copy command
          </button>
          <button
            className="action-btn primary"
            disabled={busy || !cwd.trim()}
            onClick={() => launch(false)}
          >
            {busy ? "…" : "▶ Launch in tmux"}
          </button>
        </div>
      </div>
    </>
  );
}
