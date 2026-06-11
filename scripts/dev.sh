#!/usr/bin/env bash
# Run muse in development: FastAPI backend (reload) + Vite dev server.
# Backend: http://127.0.0.1:8848   Frontend: http://127.0.0.1:5173
set -euo pipefail
cd "$(dirname "$0")/.."

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT

.venv/bin/uvicorn muse.main:app --app-dir backend --reload --port 8848 &
( cd frontend && npm run dev ) &
wait
