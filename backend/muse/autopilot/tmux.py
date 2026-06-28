"""Thin tmux transport: list panes and inject text into a pane."""

from __future__ import annotations

import subprocess


def _run(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "tmux not found"
    except subprocess.TimeoutExpired:
        return 124, "", "tmux timed out"


def available() -> bool:
    return _run(["-V"])[0] == 0


def list_panes() -> list[dict]:
    """Return [{pane_id, pane_pid, cmd, cwd}] across all tmux sessions."""
    fmt = "#{pane_id}\t#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}"
    code, out, _ = _run(["list-panes", "-a", "-F", fmt])
    if code != 0:
        return []
    panes = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            try:
                panes.append(
                    {"pane_id": parts[0], "pane_pid": int(parts[1]), "cmd": parts[2], "cwd": parts[3]}
                )
            except ValueError:
                continue
    return panes


def pane_exists(pane_id: str) -> bool:
    return any(p["pane_id"] == pane_id for p in list_panes())


def list_layout() -> list[dict]:
    """Every pane across all tmux sessions, with its session/window/pane grouping
    and active flags — enough for a phone to render the whole tmux topology and let
    the user swipe between panes. One dict per pane, in tmux's natural order."""
    fmt = "\t".join(
        [
            "#{session_name}",
            "#{window_index}",
            "#{window_name}",
            "#{window_active}",
            "#{pane_id}",
            "#{pane_index}",
            "#{pane_active}",
            "#{pane_current_command}",
            "#{pane_current_path}",
            "#{pane_title}",
            "#{session_attached}",
        ]
    )
    code, out, _ = _run(["list-panes", "-a", "-F", fmt])
    if code != 0:
        return []
    panes: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 11:
            continue
        try:
            panes.append(
                {
                    "session_name": parts[0],
                    "window_index": int(parts[1]),
                    "window_name": parts[2],
                    "window_active": parts[3] == "1",
                    "pane_id": parts[4],
                    "pane_index": int(parts[5]),
                    "pane_active": parts[6] == "1",
                    "command": parts[7],
                    "cwd": parts[8],
                    "title": parts[9],
                    "session_attached": parts[10] != "0",
                }
            )
        except ValueError:
            continue
    return panes


def new_window(cwd: str, command: str, session: str | None = None) -> tuple[bool, str]:
    """Open a new tmux window running `command` (a shell string) in `cwd`, appended
    to `session` (or tmux's current session if None). Returns (ok, pane_id | error)."""
    args = ["new-window"]
    if session:
        args += ["-t", f"{session}:"]  # append a new window to this session
    args += ["-c", cwd, "-P", "-F", "#{pane_id}", command]
    code, out, err = _run(args)
    if code != 0:
        return False, err or "tmux new-window failed"
    return True, out.strip()


def send_text(pane_id: str, text: str, submit: bool = True) -> tuple[bool, str]:
    """Type `text` into a pane (literal), then optionally press Enter to submit."""
    if not pane_id:
        return False, "no pane"
    # Send the message literally, then a separate Enter so it submits as a prompt.
    code, _, err = _run(["send-keys", "-t", pane_id, "-l", "--", text])
    if code != 0:
        return False, err or "send-keys failed"
    if submit:
        code, _, err = _run(["send-keys", "-t", pane_id, "Enter"])
        if code != 0:
            return False, err or "enter failed"
    return True, ""


def send_key(pane_id: str, key: str) -> tuple[bool, str]:
    """Send one named key (e.g. "Escape", "Enter") to a pane. Callers must
    whitelist — arbitrary keys into a live session are destructive."""
    if not pane_id:
        return False, "no pane"
    code, _, err = _run(["send-keys", "-t", pane_id, key])
    return (code == 0), (err if code != 0 else "")


def accept_suggestion(pane_id: str) -> tuple[bool, str]:
    """Accept Claude Code's inline autosuggestion (Right arrow) and submit (Enter).

    If there's no suggestion, Right is a harmless cursor move and Enter is a no-op
    on an empty prompt.
    """
    if not pane_id:
        return False, "no pane"
    code, _, err = _run(["send-keys", "-t", pane_id, "Right"])
    if code != 0:
        return False, err or "send-keys failed"
    code, _, err = _run(["send-keys", "-t", pane_id, "Enter"])
    return (code == 0), (err if code != 0 else "")


def send_digit(pane_id: str, n: int) -> tuple[bool, str]:
    """Press a single number hotkey (1-9) in a pane.

    Claude Code's permission/selection dialogs accept the numeric hotkey directly:
    a digit both highlights and confirms the option, and — unlike arrow movement —
    it does not depend on where the cursor currently sits, so it can't drift if our
    parse of the highlighted row is wrong.
    """
    if not pane_id:
        return False, "no pane"
    if not isinstance(n, int) or not (1 <= n <= 9):
        return False, f"digit out of range: {n!r}"
    code, _, err = _run(["send-keys", "-t", pane_id, str(n)])
    return (code == 0), (err if code != 0 else "")


def cycle_mode(pane_id: str) -> tuple[bool, str]:
    """Press Shift+Tab (tmux's `BTab` keysym) once to cycle Claude Code's permission
    mode (default → auto-accept edits → plan → …). One keystroke is benign and fully
    reversible; the caller re-reads the status line to report where it landed."""
    if not pane_id:
        return False, "no pane"
    code, _, err = _run(["send-keys", "-t", pane_id, "BTab"])
    return (code == 0), (err if code != 0 else "")


def select_in_menu(pane_id: str, target_index: int, current_index: int) -> tuple[bool, str]:
    """Move the highlight from `current_index` to `target_index` (0-based) and submit.

    Fallback for menus that don't take digit hotkeys (e.g. some AskUserQuestion
    multi-selects). Callers MUST pass a freshly re-parsed `current_index` — arrow
    math against a stale highlight selects the wrong row.
    """
    if not pane_id:
        return False, "no pane"
    delta = target_index - current_index
    key = "Down" if delta > 0 else "Up"
    for _ in range(abs(delta)):
        code, _, err = _run(["send-keys", "-t", pane_id, key])
        if code != 0:
            return False, err or "send-keys failed"
    code, _, err = _run(["send-keys", "-t", pane_id, "Enter"])
    return (code == 0), (err if code != 0 else "")


def capture_pane(pane_id: str, lines: int = 30) -> str:
    code, out, _ = _run(["capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"])
    return out if code == 0 else ""


def capture_visible(pane_id: str, ansi: bool = False) -> str:
    """The pane's current visible screen (no scrollback) — what's actually on the
    monitor right now. A terminal's scrollback is a jumble of previous commands and
    redraw frames, so the live screen is the only accurate, current view. Pass
    ansi=True to keep escape sequences so the UI can render real colors."""
    args = ["capture-pane", "-p", "-t", pane_id]
    if ansi:
        args.insert(1, "-e")
    code, out, _ = _run(args)
    return out if code == 0 else ""
