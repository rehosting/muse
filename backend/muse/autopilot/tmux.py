"""Thin tmux transport: list panes and inject text into a pane."""

from __future__ import annotations

import subprocess


def _run(args: list[str], timeout: float = 5.0, input: str | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=timeout, input=input
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
            "#{window_activity}",
            "#{window_id}",
        ]
    )
    code, out, _ = _run(["list-panes", "-a", "-F", fmt])
    if code != 0:
        return []
    panes: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 13:
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
                    # Epoch secs of last activity in this window — for most-recent sort.
                    "last_activity": int(parts[11]) if parts[11].isdigit() else 0,
                    # Stable window handle (@<n>) — survives index shifts, so it's the
                    # safe target for move-window.
                    "window_id": parts[12],
                }
            )
        except ValueError:
            continue
    return panes


def new_window(
    cwd: str, command: str, session: str | None = None, name: str | None = None
) -> tuple[bool, str]:
    """Open a new tmux window running `command` (a shell string) in `cwd`, appended
    to `session` (or tmux's current session if None). If `name` is given, label the
    window with it. Returns (ok, pane_id | error)."""
    args = ["new-window"]
    if session:
        args += ["-t", f"{session}:"]  # append a new window to this session
    args += ["-c", cwd, "-P", "-F", "#{pane_id}"]
    if command:  # empty command → tmux opens the default shell (restored shell windows)
        args.append(command)
    code, out, err = _run(args)
    if code != 0:
        return False, err or "tmux new-window failed"
    pane_id = out.strip()
    if name:
        # Address the window by its pane id (tmux resolves the pane's window). Best-effort:
        # rename_window also turns off automatic-rename so the label survives the running
        # command changing (claude would otherwise re-title the window to "claude").
        rename_window(pane_id, name)
    return True, pane_id


def new_session(
    name: str,
    cwd: str | None = None,
    command: str | None = None,
    window_name: str | None = None,
) -> tuple[bool, str]:
    """Create a detached tmux session `name` to act as a group. With no extras, tmux
    births it with one shell window (a scratch pane until real windows move in). When
    `cwd`/`command`/`window_name` are given, that first window is created directly running
    `command` in `cwd` — so a restored group has no leftover placeholder shell. Returns
    (ok, error)."""
    args = ["new-session", "-d", "-s", name]
    if window_name:
        args += ["-n", window_name]
    if cwd:
        args += ["-c", cwd]
    if command:
        args.append(command)
    code, _, err = _run(args)
    if code != 0:
        return False, err or "tmux new-session failed"
    if window_name:
        # Pin the label so the running command can't auto-rename it (see rename_window).
        _run(["set-window-option", "-t", f"{name}:", "automatic-rename", "off"])
    return True, ""


def rename_session(old: str, new: str) -> tuple[bool, str]:
    """Rename a group (tmux session)."""
    code, _, err = _run(["rename-session", "-t", old, new])
    return (code == 0), (err if code != 0 else "")


def kill_session(name: str) -> tuple[bool, str]:
    """Destroy a group (tmux session) and every window/pane in it. Callers must
    confirm — this kills whatever is running in the session."""
    code, _, err = _run(["kill-session", "-t", name])
    return (code == 0), (err if code != 0 else "")


def kill_window(window_id: str) -> tuple[bool, str]:
    """Destroy a single window (addressed by its stable @id) and every pane in it.
    Callers must confirm — this kills whatever is running in the window."""
    if not window_id:
        return False, "no window"
    code, _, err = _run(["kill-window", "-t", window_id])
    return (code == 0), (err if code != 0 else "")


def rename_window(window_id: str, new_name: str) -> tuple[bool, str]:
    """Rename a window (addressed by its stable @id). First disables automatic-rename
    for the window so tmux won't clobber the manual name when the running command
    changes; then sets the name."""
    if not window_id:
        return False, "no window"
    # Best-effort: keep the manual name from being auto-overwritten. Ignore failure
    # (older tmux, or the option already off) — the rename below is what matters.
    _run(["set-window-option", "-t", window_id, "automatic-rename", "off"])
    code, _, err = _run(["rename-window", "-t", window_id, new_name])
    return (code == 0), (err if code != 0 else "")


def move_window(window_id: str, dst_session: str) -> tuple[bool, str]:
    """Move a whole window (addressed by its stable @id) into `dst_session`, appending
    it after the session's current last window. The moved window's panes keep their
    processes and pane ids running — a live Claude is undisturbed. No-op (ok) if the
    window is already in the destination session."""
    if not window_id:
        return False, "no window"
    layout = list_layout()
    src = next((p for p in layout if p["window_id"] == window_id), None)
    if src is None:
        return False, f"window not found: {window_id}"
    if src["session_name"] == dst_session:
        return True, ""  # already there — nothing to do
    dst_indices = [p["window_index"] for p in layout if p["session_name"] == dst_session]
    if not dst_indices:
        return False, f"session not found: {dst_session}"
    # Append: one past the destination's current highest window index. Computing the
    # index ourselves (rather than the version-dependent -a flag) is deterministic.
    target = f"{dst_session}:{max(dst_indices) + 1}"
    code, _, err = _run(["move-window", "-s", window_id, "-t", target])
    return (code == 0), (err if code != 0 else "")


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


def paste_text(pane_id: str, text: str, submit: bool = True) -> tuple[bool, str]:
    """Deliver MULTILINE text via a bracketed paste (load-buffer → paste-buffer -p),
    then optionally submit. send-keys -l would let the raw LF bytes act as Enter
    and submit each line as its own message; a bracketed paste lands in the
    composer as one multiline message."""
    if not pane_id:
        return False, "no pane"
    code, _, err = _run(["load-buffer", "-b", "muse-paste", "-"], input=text)
    if code != 0:
        return False, err or "load-buffer failed"
    code, _, err = _run(["paste-buffer", "-p", "-d", "-b", "muse-paste", "-t", pane_id])
    if code != 0:
        return False, err or "paste-buffer failed"
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
