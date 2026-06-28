"""Outbound push notifications via a hosted service (ntfy).

muse never accepts inbound connections for this — it makes an outbound HTTP POST
to {server}/{topic}, and the ntfy app (subscribed to that topic) delivers the
push to the phone/desktop. That means it works from a localhost-only muse with
no HTTPS exposure. The topic name acts as the secret, so pick an unguessable one.

Config persists in muse's own DB (never ~/.claude).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import db
from .models import AlertRules, NotifyConfig, NotifyResult, PushSubscription

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS notify_subscriptions (
    endpoint   TEXT PRIMARY KEY,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT
);
"""

_KEY = "config"
_RULES_KEY = "rules"


def _ascii_header(value: str) -> str:
    """HTTP headers are latin-1; drop characters that can't be encoded."""
    return value.encode("latin-1", "ignore").decode("latin-1")


def send(
    cfg: NotifyConfig,
    message: str,
    *,
    title: str | None = None,
    priority: int | None = None,
    tags: str | None = None,
    click: str | None = None,
) -> NotifyResult:
    """Publish one notification. Returns ok/detail rather than raising."""
    if not cfg.enabled:
        return NotifyResult(ok=False, detail="notifications are disabled")
    if not cfg.topic.strip():
        return NotifyResult(ok=False, detail="no topic configured")
    if cfg.provider != "ntfy":
        return NotifyResult(ok=False, detail=f"unsupported provider: {cfg.provider}")

    url = f"{cfg.server.rstrip('/')}/{cfg.topic.strip()}"
    req = urllib.request.Request(url, data=(message or "").encode("utf-8"), method="POST")
    if title:
        req.add_header("Title", _ascii_header(title))
    req.add_header("Priority", str(priority if priority is not None else cfg.priority))
    if tags:
        req.add_header("Tags", _ascii_header(tags))
    if click:
        req.add_header("Click", click)
    if cfg.token:
        req.add_header("Authorization", f"Bearer {cfg.token}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            return NotifyResult(ok=ok, detail=f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        return NotifyResult(ok=False, detail=f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        return NotifyResult(ok=False, detail=f"network error: {e.reason}")
    except Exception as e:  # pragma: no cover - defensive
        return NotifyResult(ok=False, detail=str(e))


class NotifyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = db.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_config(self) -> NotifyConfig:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM notify_kv WHERE key=?", (_KEY,)
            ).fetchone()
        if not row or not row["value"]:
            return NotifyConfig()
        try:
            return NotifyConfig(**json.loads(row["value"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            return NotifyConfig()

    def set_config(self, cfg: NotifyConfig) -> NotifyConfig:
        self._put(_KEY, cfg.model_dump_json())
        return cfg

    def get_rules(self) -> AlertRules:
        raw = self._get(_RULES_KEY)
        if not raw:
            return AlertRules()
        try:
            return AlertRules(**json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            return AlertRules()

    def set_rules(self, rules: AlertRules) -> AlertRules:
        self._put(_RULES_KEY, rules.model_dump_json())
        return rules

    # --- Web Push subscriptions (one row per device) -----------------------
    def add_subscription(self, sub: "PushSubscription") -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO notify_subscriptions(endpoint, p256dh, auth, label, created_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET "
                    "p256dh=excluded.p256dh, auth=excluded.auth, label=excluded.label",
                    (
                        sub.endpoint,
                        sub.keys.get("p256dh", ""),
                        sub.keys.get("auth", ""),
                        sub.label,
                        sub.created_at,
                    ),
                )
                self._conn.commit()

        db.retry_locked(_do)

    def list_subscriptions(self) -> list["PushSubscription"]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT endpoint, p256dh, auth, label, created_at FROM notify_subscriptions"
            ).fetchall()
        return [
            PushSubscription(
                endpoint=r["endpoint"],
                keys={"p256dh": r["p256dh"], "auth": r["auth"]},
                label=r["label"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def remove_subscription(self, endpoint: str) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM notify_subscriptions WHERE endpoint=?", (endpoint,)
                )
                self._conn.commit()

        db.retry_locked(_do)

    def _get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM notify_kv WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row and row["value"] else None

    def _put(self, key: str, value: str) -> None:
        def _do() -> None:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO notify_kv(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
                self._conn.commit()

        db.retry_locked(_do)
