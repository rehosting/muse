"""Autopilot: keep active Claude Code sessions moving by injecting messages.

Strictly opt-in and gated:
- Only ever targets *active* Claude Code sessions matched to a live tmux pane
  (via ~/.claude/sessions/{pid}.json + process ancestry).
- Global "armed" switch (off by default) AND per-session enable.
- Injects only when a session is idle (finished its turn, awaiting the user) —
  never while busy, and not into permission prompts by default.
- Cooldown + max-sends + one-send-per-turn guard to avoid runaway loops.

tmux is the current transport; the controller is transport-agnostic so other
modes can be added later.
"""
