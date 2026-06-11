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


def new_window(cwd: str, command: str) -> tuple[bool, str]:
    """Open a new tmux window running `command` (a shell string) in `cwd`.
    Returns (ok, pane_id | error)."""
    code, out, err = _run(
        ["new-window", "-c", cwd, "-P", "-F", "#{pane_id}", command]
    )
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


def capture_pane(pane_id: str, lines: int = 30) -> str:
    code, out, _ = _run(["capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"])
    return out if code == 0 else ""
