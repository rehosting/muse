"""Claude Code hook integration: install/uninstall the relay, and the endpoint
file the relay reads.

Claude Code can run a command on lifecycle events (Stop, Notification, …) and
pipe the event JSON to its stdin. We install ONE tiny relay script that POSTs
that payload to muse's /api/hooks/claude — giving muse instant, authoritative
"turn ended / needs input" signals instead of 2.5–15s polling heuristics.

Design constraints:
- The relay must NEVER block or break Claude Code: 3s curl timeout, always exit 0.
- No secrets in ~/.claude/settings.json: the script reads ~/.muse/hook_endpoint.json
  (0600), which the SERVER rewrites at every startup with its live port + token —
  so token rotation or a port change never needs a reinstall.
- settings.json is merged conservatively: only our entries are added/removed;
  everything else (rtk hooks, statusline, …) is preserved byte-for-byte in spirit.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Optional

from .config import get_settings

# Events muse cares about. Stop = turn ended (deliver queued reply / alert);
# Notification = needs permission or sat idle; UserPromptSubmit = user replied
# (clears alert state instantly); SessionEnd = process exited.
HOOK_EVENTS = ["Stop", "Notification", "UserPromptSubmit", "SessionEnd"]

_SCRIPT = """#!/bin/sh
# muse hook relay (installed by `muse hooks install`).
# POSTs the Claude Code hook payload (stdin) to the local muse server.
# Always exits 0 — a muse outage must never block Claude Code.
f="$HOME/.muse/hook_endpoint.json"
[ -r "$f" ] || exit 0
url=$(sed -n 's/.*"url": *"\\([^"]*\\)".*/\\1/p' "$f")
tok=$(sed -n 's/.*"token": *"\\([^"]*\\)".*/\\1/p' "$f")
[ -n "$url" ] || exit 0
if [ -n "$tok" ]; then
  curl -s -m 3 -X POST "$url/api/hooks/claude" -H "Content-Type: application/json" \\
       -H "Authorization: Bearer $tok" --data-binary @- >/dev/null 2>&1
else
  curl -s -m 3 -X POST "$url/api/hooks/claude" -H "Content-Type: application/json" \\
       --data-binary @- >/dev/null 2>&1
fi
exit 0
"""


def state_dir() -> Path:
    return get_settings().db_path.parent


def script_path() -> Path:
    return state_dir() / "hook.sh"


def endpoint_path() -> Path:
    return state_dir() / "hook_endpoint.json"


def claude_settings_path() -> Path:
    return get_settings().claude_dir / "settings.json"


def write_endpoint_file() -> None:
    """(Re)write ~/.muse/hook_endpoint.json with the server's live URL + token.
    Called at every server startup so the relay always has current credentials."""
    s = get_settings()
    path = endpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": f"http://127.0.0.1:{s.port}",
        "token": s.resolve_auth_token() or "",
    }
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    path.chmod(0o600)


def write_script() -> Path:
    path = script_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SCRIPT, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# --- settings.json merge (pure, unit-testable) --------------------------------


def _is_ours(hook: dict, command: str) -> bool:
    return hook.get("type") == "command" and hook.get("command") == command


def add_muse_hooks(settings: dict, command: str) -> dict:
    """Return a copy of `settings` with the muse relay registered for each of
    HOOK_EVENTS. Idempotent; never touches other hooks."""
    out = json.loads(json.dumps(settings))  # deep copy, JSON-safe by construction
    hooks = out.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        matchers = hooks.setdefault(event, [])
        present = any(
            _is_ours(h, command)
            for m in matchers
            if isinstance(m, dict)
            for h in m.get("hooks", [])
            if isinstance(h, dict)
        )
        if not present:
            matchers.append({"hooks": [{"type": "command", "command": command}]})
    return out


def remove_muse_hooks(settings: dict, command: Optional[str] = None) -> dict:
    """Return a copy of `settings` with every muse relay entry stripped (matched
    by exact command, or by the hook.sh basename when command is None). Empty
    matcher groups / event lists left behind are pruned."""
    marker = command or str(script_path())
    out = json.loads(json.dumps(settings))
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event in list(hooks):
        matchers = hooks[event]
        if not isinstance(matchers, list):
            continue
        for m in matchers:
            if isinstance(m, dict) and isinstance(m.get("hooks"), list):
                m["hooks"] = [
                    h for h in m["hooks"]
                    if not (isinstance(h, dict) and h.get("type") == "command"
                            and marker in str(h.get("command", "")))
                ]
        hooks[event] = [
            m for m in matchers
            if not (isinstance(m, dict) and m.get("hooks") == [] and set(m) <= {"hooks", "matcher"})
        ]
        if hooks[event] == []:
            del hooks[event]
    if hooks == {}:
        del out["hooks"]
    return out


# --- install / uninstall / status (used by the CLI) ---------------------------


def _read_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_settings(path: Path, settings: dict) -> None:
    # Keep a timestamped backup under ~/.muse (never clobber the user's config
    # without a way back).
    if path.is_file():
        backups = state_dir() / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / f"settings.json.{int(time.time())}").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.muse-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install() -> str:
    script = write_script()
    path = claude_settings_path()
    settings = _read_settings(path)
    merged = add_muse_hooks(settings, str(script))
    if merged != settings:
        _write_settings(path, merged)
    if not endpoint_path().is_file():
        # The server rewrites this at startup; seed it now so hooks work
        # immediately if muse is already running on the default port.
        try:
            write_endpoint_file()
        except OSError:
            pass
    return (
        f"Installed muse hooks ({', '.join(HOOK_EVENTS)}) → {script}\n"
        f"Relay target: {endpoint_path()} (rewritten at every muse start).\n"
        f"New Claude Code sessions pick this up automatically; running ones on next launch."
    )


def uninstall() -> str:
    path = claude_settings_path()
    settings = _read_settings(path)
    stripped = remove_muse_hooks(settings)
    if stripped != settings:
        _write_settings(path, stripped)
    try:
        script_path().unlink()
    except OSError:
        pass
    return "Removed muse hooks from Claude Code settings."


def status() -> dict:
    """Local install state (no server round-trip)."""
    script = script_path()
    settings = _read_settings(claude_settings_path()) if claude_settings_path().is_file() else {}
    installed_events = [
        event
        for event, matchers in (settings.get("hooks") or {}).items()
        if isinstance(matchers, list)
        and any(
            str(script) in str(h.get("command", ""))
            for m in matchers
            if isinstance(m, dict)
            for h in m.get("hooks", [])
            if isinstance(h, dict)
        )
    ]
    return {
        "script_exists": script.is_file(),
        "endpoint_file_exists": endpoint_path().is_file(),
        "installed_events": sorted(installed_events),
        "expected_events": HOOK_EVENTS,
    }
