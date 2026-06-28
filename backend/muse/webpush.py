"""Web Push (VAPID) — an additional, outbound-only notification channel.

A device subscribes once while connected to muse; thereafter the browser's push
service (Apple/Google/Mozilla) delivers notifications over the public internet,
so the phone receives them whether or not it is currently on the tailnet. muse
only needs *outbound* reachability to the subscription's push endpoint — it never
accepts inbound connections for this.

The VAPID keypair is generated on first use and persisted next to muse's DB
(private key chmod 0600). The public key is handed to the browser to subscribe.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import b64urlencode
from pywebpush import WebPushException, webpush

from .config import get_settings

_lock = threading.Lock()
# Push services reject claims with an absurd lifetime; the sub is informational.
_CLAIMS_SUB = "mailto:muse@localhost"


def _key_path() -> Path:
    return get_settings().db_path.parent / "vapid_private.pem"


@lru_cache(maxsize=1)
def get_or_create_vapid_keys() -> dict:
    """Return {public_key, private_path}. Generates + persists the keypair once."""
    path = _key_path()
    with _lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            path.write_bytes(pem)
            path.chmod(0o600)
        else:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)

    # The browser's applicationServerKey is the uncompressed public point, b64url.
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return {"public_key": b64urlencode(raw), "private_path": str(path)}


def send_web_push(subscription: dict, payload: str) -> tuple[bool, bool]:
    """Deliver one push. Returns (ok, expired). `expired` => the subscription is
    dead (404/410) and the caller should prune it."""
    keys = get_or_create_vapid_keys()
    try:
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=keys["private_path"],
            vapid_claims={"sub": _CLAIMS_SUB},
            timeout=10,
        )
        return True, False
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        return False, status in (404, 410)
    except Exception:  # pragma: no cover - defensive
        return False, False
