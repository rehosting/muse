import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AlertEvent, AlertRules, NotifyConfig, NotifyResult } from "../api/types";

const DEFAULT: NotifyConfig = {
  enabled: false,
  provider: "ntfy",
  server: "https://ntfy.sh",
  topic: "",
  priority: 3,
  token: null,
};

const DEFAULT_RULES: AlertRules = {
  on_waiting: true,
  on_stopped: false,
  on_error: true,
  poll_seconds: 15,
};

function randomTopic(): string {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let s = "";
  for (let i = 0; i < 10; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return `muse-${s}`;
}

export default function AlertsPage() {
  const [cfg, setCfg] = useState<NotifyConfig>(DEFAULT);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<NotifyResult | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [rules, setRules] = useState<AlertRules>(DEFAULT_RULES);
  const [log, setLog] = useState<AlertEvent[]>([]);

  useEffect(() => {
    api
      .getNotifyConfig()
      .then((c) => setCfg({ ...DEFAULT, ...c }))
      .catch(() => setCfg(DEFAULT))
      .finally(() => setLoaded(true));
    api.getAlertRules().then((r) => setRules({ ...DEFAULT_RULES, ...r })).catch(() => {});
  }, []);

  // Poll the recent-alerts log so fired notifications show up here too.
  useEffect(() => {
    let active = true;
    const tick = () => api.getAlertLog().then((l) => active && setLog(l)).catch(() => {});
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const setRule = <K extends keyof AlertRules>(k: K, v: AlertRules[K]) => {
    const next = { ...rules, [k]: v };
    setRules(next);
    api.setAlertRules(next).catch(() => {});
  };

  const set = <K extends keyof NotifyConfig>(k: K, v: NotifyConfig[K]) =>
    setCfg((c) => ({ ...c, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      const saved = await api.setNotifyConfig(cfg);
      setCfg({ ...DEFAULT, ...saved });
      setSavedAt(Date.now());
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(await api.testNotify(cfg));
    } catch (e) {
      setResult({ ok: false, detail: String(e) });
    } finally {
      setTesting(false);
    }
  };

  const subscribeUrl = cfg.topic ? `${cfg.server.replace(/\/$/, "")}/${cfg.topic}` : "";

  if (!loaded) return <div className="empty">Loading…</div>;

  return (
    <div className="alerts-wrap">
      <h1 className="alerts-title">Alerts &amp; Push Notifications</h1>
      <p className="alerts-sub">
        muse sends notifications to your phone via <strong>ntfy</strong> — an outbound-only push
        service, so this works even though muse runs on localhost. The topic name is the secret;
        keep it unguessable.
      </p>

      <div className="alerts-card">
        <div className="alerts-row">
          <label className="alerts-toggle">
            <input
              type="checkbox"
              checked={cfg.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
            <span>Enable push notifications</span>
          </label>
        </div>

        <div className="alerts-field">
          <label>Topic (secret)</label>
          <div className="alerts-inline">
            <input
              value={cfg.topic}
              placeholder="muse-xxxxxxxxxx"
              onChange={(e) => set("topic", e.target.value.trim())}
            />
            <button className="action-btn" onClick={() => set("topic", randomTopic())}>
              Generate
            </button>
          </div>
        </div>

        <div className="alerts-field">
          <label>Server</label>
          <input value={cfg.server} onChange={(e) => set("server", e.target.value.trim())} />
          <span className="alerts-hint">Default ntfy.sh; change only if self-hosting.</span>
        </div>

        <div className="alerts-field">
          <label>Priority</label>
          <select
            value={cfg.priority}
            onChange={(e) => set("priority", Number(e.target.value))}
          >
            <option value={1}>1 — min</option>
            <option value={2}>2 — low</option>
            <option value={3}>3 — default</option>
            <option value={4}>4 — high</option>
            <option value={5}>5 — max / urgent</option>
          </select>
        </div>

        <div className="alerts-field">
          <label>Auth token (optional)</label>
          <input
            value={cfg.token ?? ""}
            placeholder="only for protected / self-hosted topics"
            onChange={(e) => set("token", e.target.value || null)}
          />
        </div>

        <div className="alerts-actions">
          <button className="action-btn primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="action-btn" onClick={test} disabled={testing || !cfg.topic}>
            {testing ? "Sending…" : "Send test"}
          </button>
          {savedAt && <span className="alerts-ok">Saved ✓</span>}
          {result && (
            <span className={result.ok ? "alerts-ok" : "alerts-err"}>
              {result.ok ? "Test sent ✓ — check your phone" : `Failed: ${result.detail}`}
            </span>
          )}
        </div>
      </div>

      <div className="alerts-card">
        <h2 className="alerts-h2">When to notify me</h2>
        <p className="alerts-hint" style={{ marginBottom: 12 }}>
          A background watcher checks your sessions and pushes when:
        </p>
        <label className="alerts-toggle">
          <input
            type="checkbox"
            checked={rules.on_waiting}
            onChange={(e) => setRule("on_waiting", e.target.checked)}
          />
          <span>✋ a session finishes its turn and is waiting for you</span>
        </label>
        <label className="alerts-toggle">
          <input
            type="checkbox"
            checked={rules.on_error}
            onChange={(e) => setRule("on_error", e.target.checked)}
          />
          <span>⚠ a session hits an error (tool / API / system)</span>
        </label>
        <label className="alerts-toggle">
          <input
            type="checkbox"
            checked={rules.on_stopped}
            onChange={(e) => setRule("on_stopped", e.target.checked)}
          />
          <span>⏹ a session goes idle / stops</span>
        </label>
        <div className="alerts-field" style={{ marginTop: 12, maxWidth: 220 }}>
          <label>Check interval (seconds)</label>
          <input
            type="number"
            min={5}
            value={rules.poll_seconds}
            onChange={(e) => setRule("poll_seconds", Math.max(5, Number(e.target.value) || 15))}
          />
        </div>
        <p className="alerts-hint">Rules save automatically. Delivery still requires the toggle above to be on.</p>
      </div>

      {log.length > 0 && (
        <div className="alerts-card">
          <h2 className="alerts-h2">Recent alerts</h2>
          <div className="alerts-log">
            {log.map((e, i) => (
              <div key={i} className="alerts-log-row">
                <span className={`alerts-log-dot ${e.kind}`} />
                <span className="alerts-log-msg">{e.message}</span>
                <span className="alerts-log-meta">
                  {e.delivered ? "sent" : `not sent · ${e.detail}`} ·{" "}
                  {new Date(e.ts).toLocaleTimeString([], { hour12: false })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="alerts-card">
        <h2 className="alerts-h2">Set up your phone</h2>
        <ol className="alerts-steps">
          <li>
            Install the <strong>ntfy</strong> app —{" "}
            <a href="https://apps.apple.com/app/ntfy/id1625396347" target="_blank" rel="noreferrer">
              iOS
            </a>{" "}
            ·{" "}
            <a
              href="https://play.google.com/store/apps/details?id=io.heckel.ntfy"
              target="_blank"
              rel="noreferrer"
            >
              Android
            </a>
            .
          </li>
          <li>
            In the app, tap <em>Subscribe to topic</em> and enter your topic:{" "}
            <code>{cfg.topic || "(set a topic above)"}</code>
          </li>
          <li>Click <em>Send test</em> above — the notification should arrive on your phone.</li>
        </ol>
        {subscribeUrl && (
          <p className="alerts-hint">
            Web/desktop: open <a href={subscribeUrl} target="_blank" rel="noreferrer">{subscribeUrl}</a>
          </p>
        )}
      </div>
    </div>
  );
}
