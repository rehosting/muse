import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  AutopilotSession,
  AutopilotState,
  ContextAction,
  IdleMode,
} from "../api/types";
import { relativeTime } from "../util/format";
import { usePolling } from "../hooks/usePolling";

const EMPTY_STATE: AutopilotState = {
  armed: false,
  tmux_available: true,
  schedule_enabled: false,
  schedule_start_hour: 22,
  schedule_end_hour: 7,
  within_hours: true,
  sessions: [],
  recent_log: [],
};

const DEFAULT_POLICY = {
  enabled: true,
  idle_mode: "message" as IdleMode,
  message: "continue",
  max_sends: 5,
  interval_seconds: 30,
  context_threshold_pct: 80,
  context_action: "compact" as ContextAction,
  context_message: "",
  backoff_seconds: 900,
};

type Draft = typeof DEFAULT_POLICY;

export default function AutopilotPage() {
  // Render the shell immediately with an empty state; fill in as data arrives.
  const [state, setState] = useState<AutopilotState>(EMPTY_STATE);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [draft, setDraft] = useState<Draft>(DEFAULT_POLICY);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await api.getAutopilot());
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  usePolling(refresh, 5000);

  // When exactly one session is selected, load its policy into the editor.
  const single = selected.size === 1 ? [...selected][0] : null;
  useEffect(() => {
    if (!single) return;
    const s = state.sessions.find((x) => x.session_id === single);
    if (s) {
      const c = s.config;
      setDraft({
        enabled: c.enabled,
        idle_mode: c.idle_mode,
        message: c.message,
        max_sends: c.max_sends,
        interval_seconds: c.interval_seconds,
        context_threshold_pct: c.context_threshold_pct,
        context_action: c.context_action,
        context_message: c.context_message,
        backoff_seconds: c.backoff_seconds,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [single]);

  const sessions = state.sessions;
  const liveIds = useMemo(
    () => sessions.filter((s) => s.live?.pane_id).map((s) => s.session_id),
    [sessions],
  );

  const arm = async (armed: boolean) => setState(await api.armAutopilot(armed));
  const saveSchedule = async (enabled: boolean, start: number, end: number) =>
    setState(await api.setAutopilotSchedule(enabled, start, end));
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const apply = async (enabled: boolean) => {
    if (!selected.size) return;
    const next = await api.applyAutopilotPolicy({
      session_ids: [...selected],
      ...draft,
      enabled,
    });
    setState(next);
    setSaveMsg(enabled ? `Applied to ${selected.size} session(s)` : "Disabled");
    setTimeout(() => setSaveMsg(null), 2500);
  };

  const sendNow = async (sid: string) => {
    try {
      await api.autopilotSend(sid);
      setSaveMsg("sent ✓");
    } catch (e) {
      setSaveMsg(String(e).replace(/^Error:\s*/, ""));
    }
    setTimeout(() => setSaveMsg(null), 3000);
  };

  return (
    <div className="ap-layout">
      {/* ---- left: selectable sessions ---- */}
      <aside className="ap-side">
        <div className={`ap-arm ${state.armed ? "armed" : ""}`}>
          <div>
            <div className="ap-arm-state">{state.armed ? "● ARMED" : "○ Disarmed"}</div>
          </div>
          <button className={`ap-arm-btn ${state.armed ? "on" : ""}`} onClick={() => arm(!state.armed)}>
            {state.armed ? "Disarm" : "Arm"}
          </button>
        </div>

        <ScheduleBox state={state} onSave={saveSchedule} />

        <div className="ap-side-head">
          <span>Sessions</span>
          <button
            className="linkish"
            onClick={() => setSelected(new Set(liveIds))}
            disabled={!liveIds.length}
          >
            select all live
          </button>
        </div>

        {!state.tmux_available && <div className="error-banner">tmux not found</div>}
        {error && <div className="error-banner">{error}</div>}

        <div className="ap-list">
          {sessions.map((s) => (
            <SidebarRow
              key={s.session_id}
              s={s}
              selected={selected.has(s.session_id)}
              onToggle={() => toggle(s.session_id)}
            />
          ))}
          {sessions.length === 0 && (
            <div className="empty">{loaded ? "No sessions." : "Discovering sessions…"}</div>
          )}
        </div>
      </aside>

      {/* ---- right: policy editor + log ---- */}
      <main className="ap-main">
        {selected.size === 0 ? (
          <div className="empty">
            Select one or more sessions on the left to apply a policy.
          </div>
        ) : (
          <PolicyEditor
            count={selected.size}
            single={single ? sessions.find((x) => x.session_id === single) ?? null : null}
            draft={draft}
            setDraft={setDraft}
            onApply={apply}
            onSendNow={sendNow}
            saveMsg={saveMsg}
          />
        )}

        <h3 className="stat-section">Recent activity</h3>
        <div className="ap-log">
          {state.recent_log.length === 0 && <div className="empty">No activity yet.</div>}
          {state.recent_log.map((l, i) => (
            <div className={`ap-log-row act-${l.action}`} key={i}>
              <span className="ap-log-ts">{relativeTime(l.ts)}</span>
              <span className="ap-log-action">{l.action}</span>
              <span className="ap-log-sid">{l.session_id.slice(0, 8)}</span>
              <span className="ap-log-detail">{l.detail}</span>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}

function ScheduleBox({
  state,
  onSave,
}: {
  state: AutopilotState;
  onSave: (enabled: boolean, start: number, end: number) => void;
}) {
  const [start, setStart] = useState(state.schedule_start_hour);
  const [end, setEnd] = useState(state.schedule_end_hour);
  const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;
  return (
    <div className="ap-sched">
      <label className="ap-sched-top">
        <input
          type="checkbox"
          checked={state.schedule_enabled}
          onChange={(e) => onSave(e.target.checked, start, end)}
        />
        active hours only
      </label>
      <div className="ap-sched-row">
        <select value={start} onChange={(e) => { const v = Number(e.target.value); setStart(v); onSave(state.schedule_enabled, v, end); }}>
          {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hh(h)}</option>)}
        </select>
        <span>→</span>
        <select value={end} onChange={(e) => { const v = Number(e.target.value); setEnd(v); onSave(state.schedule_enabled, start, v); }}>
          {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hh(h)}</option>)}
        </select>
      </div>
      {state.schedule_enabled && (
        <div className={`ap-sched-now ${state.within_hours ? "in" : "out"}`}>
          {state.within_hours ? "● within active hours" : "○ outside active hours — paused"}
        </div>
      )}
    </div>
  );
}

function statusClass(status: string): string {
  if (status === "idle") return "st-idle";
  if (status === "busy") return "st-busy";
  if (status === "waiting") return "st-waiting";
  return "st-other";
}

function SidebarRow({
  s,
  selected,
  onToggle,
}: {
  s: AutopilotSession;
  selected: boolean;
  onToggle: () => void;
}) {
  const live = s.live;
  return (
    <button className={`ap-row ${selected ? "sel" : ""}`} onClick={onToggle}>
      <span className={`ap-dot ${s.config.enabled ? "on" : ""}`} />
      <span className="ap-row-title">{s.title ?? s.session_id.slice(0, 8)}</span>
      {live ? (
        <span className={`state-badge ${statusClass(live.status)}`}>{live.status}</span>
      ) : (
        <span className="state-badge st-offline">offline</span>
      )}
    </button>
  );
}

function PolicyEditor({
  count,
  single,
  draft,
  setDraft,
  onApply,
  onSendNow,
  saveMsg,
}: {
  count: number;
  single: AutopilotSession | null;
  draft: Draft;
  setDraft: (d: Draft) => void;
  onApply: (enabled: boolean) => void;
  onSendNow: (sid: string) => void;
  saveMsg: string | null;
}) {
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draft, [k]: v });
  const cfg = single?.config;

  return (
    <div className="ap-editor">
      <div className="ap-editor-head">
        <h3>
          Policy {single ? `· ${single.title ?? single.session_id.slice(0, 8)}` : `· ${count} sessions`}
        </h3>
        {single?.live?.pane_id && <span className="ap-pane">tmux {single.live.pane_id}</span>}
        {cfg && (
          <span className="ap-count">
            sent {cfg.sent_count}/{cfg.max_sends}
            {cfg.backoff_until ? ` · backing off until ${relativeTime(cfg.backoff_until)}` : ""}
          </span>
        )}
      </div>

      <label className="ap-field">
        <span>When idle</span>
        <select value={draft.idle_mode} onChange={(e) => set("idle_mode", e.target.value as IdleMode)}>
          <option value="message">send a fixed message</option>
          <option value="suggestion">accept Claude's suggested follow-up (experimental)</option>
        </select>
      </label>

      {draft.idle_mode === "message" ? (
        <label className="ap-field">
          <span>Message</span>
          <textarea
            className="ap-message"
            placeholder="e.g. 'continue', or detailed next-step instructions…"
            value={draft.message}
            onChange={(e) => set("message", e.target.value)}
          />
        </label>
      ) : (
        <p className="ap-hint">
          Autopilot will accept Claude Code's inline suggested prompt (→) and submit it. If there's
          no suggestion, nothing is sent.
        </p>
      )}

      <div className="ap-row2">
        <label className="ap-field sm">
          <span>Max sends</span>
          <input type="number" min={1} value={draft.max_sends} onChange={(e) => set("max_sends", Number(e.target.value))} />
        </label>
        <label className="ap-field sm">
          <span>Min interval (s)</span>
          <input type="number" min={5} value={draft.interval_seconds} onChange={(e) => set("interval_seconds", Number(e.target.value))} />
        </label>
      </div>

      <fieldset className="ap-fieldset">
        <legend>When context fills up (compaction)</legend>
        <div className="ap-row2">
          <label className="ap-field sm">
            <span>At ≥ %</span>
            <input type="number" min={1} max={100} value={draft.context_threshold_pct} onChange={(e) => set("context_threshold_pct", Number(e.target.value))} />
          </label>
          <label className="ap-field">
            <span>Do</span>
            <select value={draft.context_action} onChange={(e) => set("context_action", e.target.value as ContextAction)}>
              <option value="compact">/compact (summarize &amp; keep going)</option>
              <option value="clear">/clear (wipe context)</option>
              <option value="message">send a custom message</option>
              <option value="stop">stop autopilot for this session</option>
              <option value="none">nothing</option>
            </select>
          </label>
        </div>
        {draft.context_action === "message" && (
          <textarea
            className="ap-message"
            placeholder="Message to send when context is full (e.g. '/compact then continue with the plan')…"
            value={draft.context_message}
            onChange={(e) => set("context_message", e.target.value)}
          />
        )}
      </fieldset>

      <fieldset className="ap-fieldset">
        <legend>When usage limit is hit</legend>
        <label className="ap-field sm">
          <span>Back off for (s)</span>
          <input type="number" min={30} value={draft.backoff_seconds} onChange={(e) => set("backoff_seconds", Number(e.target.value))} />
        </label>
        <span className="ap-hint">
          If a rate/usage-limit message is detected in the pane, autopilot pauses this session for the
          back-off window instead of hammering it.
        </span>
      </fieldset>

      <div className="ap-actions">
        <button className="ap-arm-btn on" onClick={() => onApply(true)}>
          Apply &amp; enable ({count})
        </button>
        <button className="action-btn" onClick={() => onApply(false)}>
          Apply &amp; disable
        </button>
        {single?.live?.pane_id && (
          <button
            className="action-btn"
            disabled={draft.idle_mode === "message" && !draft.message.trim()}
            onClick={() => onSendNow(single.session_id)}
          >
            Send now
          </button>
        )}
        {saveMsg && <span className="ap-sendmsg">{saveMsg}</span>}
      </div>
    </div>
  );
}
