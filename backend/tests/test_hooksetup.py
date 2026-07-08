"""Hook installer: conservative settings.json merge, idempotence, uninstall."""

from muse import hooksetup

CMD = "/home/u/.muse/hook.sh"

RTK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}


def test_add_registers_all_events_and_preserves_existing():
    settings = {
        "model": "claude-fable-5",
        "hooks": {"PreToolUse": [RTK], "Stop": [{"hooks": [{"type": "command", "command": "other"}]}]},
    }
    out = hooksetup.add_muse_hooks(settings, CMD)
    # untouched keys and foreign hooks survive
    assert out["model"] == "claude-fable-5"
    assert out["hooks"]["PreToolUse"] == [RTK]
    assert out["hooks"]["Stop"][0]["hooks"][0]["command"] == "other"
    # ours added for every event
    for event in hooksetup.HOOK_EVENTS:
        cmds = [
            h["command"]
            for m in out["hooks"][event]
            for h in m.get("hooks", [])
        ]
        assert CMD in cmds
    # input not mutated
    assert "Notification" not in settings["hooks"]


def test_add_is_idempotent():
    once = hooksetup.add_muse_hooks({}, CMD)
    twice = hooksetup.add_muse_hooks(once, CMD)
    assert once == twice


def test_remove_strips_only_ours():
    merged = hooksetup.add_muse_hooks(
        {"hooks": {"PreToolUse": [RTK]}, "theme": "dark"}, CMD
    )
    out = hooksetup.remove_muse_hooks(merged, CMD)
    assert out["hooks"] == {"PreToolUse": [RTK]}
    assert out["theme"] == "dark"
    # removing from a settings dict with no hooks is a no-op
    assert hooksetup.remove_muse_hooks({"a": 1}, CMD) == {"a": 1}


def test_remove_then_add_roundtrip():
    base = {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "notify.cjs"}]}]}}
    merged = hooksetup.add_muse_hooks(base, CMD)
    assert hooksetup.remove_muse_hooks(merged, CMD) == base
