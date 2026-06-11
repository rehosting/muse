#!/usr/bin/env bash
# Cleanly (re)start the muse server: stops any live instance via its pidfile, then
# starts fresh. This is the canonical way to apply code changes — muse has no
# --reload in production and refuses to start alongside a live instance.
#
# Usage: scripts/restart.sh [start|stop|restart|status]   (default: restart)
set -euo pipefail
cd "$(dirname "$0")/.."

CMD="${1:-restart}"

# Prefer the installed `muse` console script; fall back to running the module from
# the source tree (no install needed).
if [ -x ".venv/bin/muse" ]; then
    exec .venv/bin/muse "$CMD"
else
    exec env PYTHONPATH=backend .venv/bin/python -m muse.cli "$CMD"
fi
