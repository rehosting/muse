import { useCallback, useEffect, useState } from "react";

/** Token prompt for remote access. Local use never sees this: with no token
 * configured (or from loopback) every request succeeds and the gate stays
 * closed. On a 401 (event from the api client) or an unauthenticated probe,
 * it overlays a single token input; success sets the HttpOnly cookie and
 * reloads so every query/stream restarts authenticated. */
export default function LoginGate() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const probe = useCallback(async () => {
    try {
      const res = await fetch("/api/auth/status");
      const s = (await res.json()) as { auth_required: boolean; authenticated: boolean };
      if (s.auth_required && !s.authenticated) setOpen(true);
    } catch {
      /* server unreachable — not an auth problem */
    }
  }, []);

  useEffect(() => {
    probe();
    const onRequired = () => setOpen(true);
    window.addEventListener("muse:auth-required", onRequired);
    return () => window.removeEventListener("muse:auth-required", onRequired);
  }, [probe]);

  if (!open) return null;

  const submit = async () => {
    const t = token.trim();
    if (!t || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: t }),
      });
      if (res.status === 204) {
        location.reload();
        return;
      }
      setError(res.status === 403 ? "Wrong token." : `Login failed (${res.status}).`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-overlay">
      <div className="login-card">
        <div className="login-title">✻ muse</div>
        <p className="dim">
          This muse is protected. Paste the token from{" "}
          <code>~/.muse/auth_token</code> (or <code>MUSE_AUTH_TOKEN</code>) on
          the host.
        </p>
        <input
          type="password"
          className="login-input"
          autoFocus
          placeholder="auth token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <button className="action-btn primary" disabled={busy || !token.trim()} onClick={submit}>
          {busy ? "…" : "Unlock"}
        </button>
        {error && <div className="login-error">⚠ {error}</div>}
      </div>
    </div>
  );
}
