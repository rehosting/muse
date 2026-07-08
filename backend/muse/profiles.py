"""Launch profiles: named templates for opening a new tmux window.

A profile pins a working directory, runs an arbitrary shell `command` (typically a
`setup && claude` chain), and may declare `params` that muse collects in a small dialog
and substitutes into the command before launch. Profiles are hand-authored in a TOML file
(``~/.muse/profiles.toml`` by default) — muse only READS them; there is no in-app editor.

The plain "new claude window" is just the built-in ``Claude`` default profile, so the
feature degrades gracefully when no config file exists.
"""

from __future__ import annotations

import fnmatch
import os
import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from .config import get_settings

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # 3.10 — use the backport
    import tomli as tomllib


class ProfileError(Exception):
    """A profiles.toml that can't be read/parsed/validated. Surfaced to the UI as 400."""


class ProfileParam(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    prompt: str
    default: str = ""


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    cwd: str = "~"
    command: str = "claude"
    params: list[ProfileParam] = []
    window_name: str = ""  # tmux window label template; {key} params substituted
    # Session-removal cleanup (see match_cleanup): a window whose cwd matches match_cwd
    # belongs to this profile; `cleanup` is the teardown shell command run on removal.
    match_cwd: str = ""  # glob (e.g. "~/workspace/igloo-dev/projects/*")
    cleanup: str = ""  # command run from the profile's cwd; {basename}/{window}/{cwd} subbed
    builtin: bool = False


# Always available so "New window" works with no config file. A file profile named
# "Claude" (case-insensitive) overrides this.
DEFAULT_PROFILE = Profile(name="Claude", cwd="~", command="claude", builtin=True)


def profiles_path() -> Path:
    """Where profiles are read from. ``MUSE_PROFILES_PATH`` overrides; otherwise it sits
    beside muse's DB (``~/.muse/profiles.toml``). Reads the env directly rather than the
    lru-cached Settings so tests can point it at a tmp file per-test."""
    env = os.environ.get("MUSE_PROFILES_PATH")
    if env:
        return Path(env).expanduser()
    return get_settings().db_path.parent / "profiles.toml"


def load_profiles() -> list[Profile]:
    """Built-in default first, then user profiles from the TOML file (in file order).

    Raises ``ProfileError`` on a decode error, a non-array ``profile`` key, a duplicate
    name, or a schema failure — the router turns that into a 400 so a broken config shows
    an actionable message instead of silently doing nothing."""
    path = profiles_path()
    if not path.exists():
        return [DEFAULT_PROFILE]

    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        raise ProfileError(f"could not read {path}: {e}") from e

    raw = data.get("profile", [])
    if not isinstance(raw, list):
        raise ProfileError("profiles.toml: expected an array of [[profile]] tables")

    file_profiles: list[Profile] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ProfileError(f"profile #{i + 1}: expected a [[profile]] table")
        try:
            p = Profile.model_validate({**item, "builtin": False})
        except ValidationError as e:
            raise ProfileError(f"profile #{i + 1}: {e}") from e
        low = p.name.lower()
        if low in seen:
            raise ProfileError(f"duplicate profile name: {p.name!r}")
        seen.add(low)
        file_profiles.append(p)

    # File wins: only prepend the built-in default when the file doesn't define "Claude".
    out: list[Profile] = []
    if DEFAULT_PROFILE.name.lower() not in seen:
        out.append(DEFAULT_PROFILE)
    out.extend(file_profiles)
    return out


def find_profile(name: str) -> Profile | None:
    for p in load_profiles():
        if p.name == name:
            return p
    return None


def render(profile: Profile, values: dict[str, str]) -> tuple[str, str]:
    """Return ``(cwd, command)`` with declared ``{key}`` params substituted.

    cwd params are substituted RAW — cwd is passed to tmux as a literal ``-c`` argument
    that is not shell-interpreted. command params are ``shlex.quote``d — command is a
    shell string tmux runs, so a value with a space/metachar becomes one safe token.
    Missing values fall back to the param's ``default``."""
    cwd, command = profile.cwd, profile.command
    for param in profile.params:
        val = values.get(param.key, param.default)
        cwd = cwd.replace("{" + param.key + "}", val)
        command = command.replace("{" + param.key + "}", shlex.quote(val))
    return os.path.expanduser(cwd), command


def window_label(profile: Profile, values: dict[str, str]) -> str:
    """The tmux window name for a launched profile — so a new window is easy to spot
    instead of the generic "claude". Uses the profile's ``window_name`` template if set;
    otherwise the first param's value (e.g. the issue name), else the profile name.
    ``{key}`` params are substituted raw (it's a label, not shell); the result is
    whitespace-collapsed and truncated to a sane tmux width."""
    tmpl = profile.window_name
    if not tmpl:
        tmpl = "{" + profile.params[0].key + "}" if profile.params else profile.name
    for param in profile.params:
        tmpl = tmpl.replace("{" + param.key + "}", values.get(param.key, param.default))
    return " ".join(tmpl.split())[:40]


def match_cleanup(cwd: str, window_name: str) -> dict | None:
    """Match a running window (by its cwd) to a profile's cleanup, for session removal.

    Returns ``{profile, command, run_cwd}`` for the first profile whose ``match_cwd`` glob
    matches ``cwd`` and that defines a ``cleanup`` command, or None. The cleanup command's
    window-derived vars — ``{basename}`` (of the cwd), ``{window}`` (name), ``{cwd}`` — are
    shell-quoted (cleanup runs via a shell); the launch params aren't persisted, so cleanup
    references these instead. It runs from the profile's cwd (where its scripts live)."""
    if not cwd:
        return None
    for p in load_profiles():
        if not (p.cleanup and p.match_cwd):
            continue
        if not fnmatch.fnmatch(os.path.expanduser(cwd), os.path.expanduser(p.match_cwd)):
            continue
        subs = {
            "cwd": cwd,
            "basename": os.path.basename(cwd.rstrip("/")),
            "window": window_name or "",
        }
        command = p.cleanup
        for k, v in subs.items():
            command = command.replace("{" + k + "}", shlex.quote(v))
        return {"profile": p.name, "command": command, "run_cwd": os.path.expanduser(p.cwd)}
    return None


def run_cleanup(command: str, run_cwd: str) -> tuple[bool, str]:
    """Run a profile cleanup command (trusted user config, like the launch command) headless
    in ``run_cwd``. Returns (ok, combined stdout+stderr, trimmed). Bounded by a timeout so a
    hung teardown can't wedge the request."""
    try:
        p = subprocess.run(
            command,
            shell=True,
            cwd=run_cwd if os.path.isdir(run_cwd) else None,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "cleanup timed out after 120s"
    out = (p.stdout + p.stderr).strip()
    return p.returncode == 0, out[-2000:]
