"""The pane key bar sends named keys, literal characters, modifier combos, and
function keys through /api/tmux/panes/{id}/key. _resolve_key is the allowlist +
normalizer that turns a UI key string into a tmux send-keys argument."""

import pytest

from muse.routers.tmux import _resolve_key


@pytest.mark.parametrize(
    "raw,expected",
    [
        # named navigation keys → tmux key names, sent as keys (not literal)
        ("escape", ("Escape", False)),
        ("tab", ("Tab", False)),
        ("left", ("Left", False)),
        ("right", ("Right", False)),
        ("home", ("Home", False)),
        ("end", ("End", False)),
        ("pageup", ("PageUp", False)),
        ("pagedown", ("PageDown", False)),
        ("delete", ("DC", False)),  # Delete key (terminal passthrough)
        # printable characters → sent literally (send-keys -l)
        ("/", ("/", True)),
        ("|", ("|", True)),
        ("-", ("-", True)),
        # modifier combos
        ("c-c", ("C-c", False)),
        ("m-f", ("M-f", False)),
        ("m-b", ("M-b", False)),  # Alt+char passthrough → meta
        ("c-left", ("C-Left", False)),
        ("c-m-a", ("C-M-a", False)),
        ("s-tab", ("S-Tab", False)),  # Shift+Tab passthrough
        ("c-space", ("C-Space", False)),  # Ctrl+Space passthrough
        # function keys
        ("f1", ("F1", False)),
        ("f12", ("F12", False)),
    ],
)
def test_resolve_key_allowed(raw, expected):
    assert _resolve_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "nope", "f13", "c-", "ctrl", "arrowup"])
def test_resolve_key_rejects(raw):
    assert _resolve_key(raw) is None
