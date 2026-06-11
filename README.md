# muse

A read-only web app to investigate what Claude Code is doing. It reconstructs
Claude Code session transcripts from `~/.claude/projects`, showing:

- a front page listing every session (across all projects) with a short title,
- a session viewer that reconstructs the conversation, tool calls, and a rich
  per-tool detail panel (diffs for edits, syntax-highlighted file reads,
  command+output for Bash, token usage, collapsible large outputs),
- subagent drill-down: follow `Agent`/`Task` invocations into their own
  transcripts and navigate back via a breadcrumb,
- live tailing: running sessions stream new messages/tool calls in near
  real-time over SSE.

muse never writes to `~/.claude`.

## Architecture

```
backend/muse/         FastAPI service (Python)
  models.py           Pydantic types = the API contract
  paths.py            ~/.claude/projects path encoding/decoding
  parser.py           tolerant JSONL line -> normalized ThreadItem
  transcript.py       assemble a Thread (tool pairing, subagent refs, title)
  discovery.py        scan projects -> SessionSummary[] (mtime-cached)
  persisted.py        <persisted-output> detection (large tool results)
  tailer.py           polling file-watch -> incremental append/tool_result events
  services/
    events.py         async pub/sub broker (shared by SSE and future jobs)
    session_service.py the seam routers call (no logic in routers)
  routers/            REST + SSE endpoints
  jobs/               FUTURE stub: job queue + tmux injection
frontend/src/         React + Vite (TypeScript) SPA
```

### How it reads the on-disk format

- Main transcript: `~/.claude/projects/{encoded-cwd}/{sessionId}.jsonl`
  (encoded-cwd = absolute path with `/`→`-`, leading `-`).
- Subagents: `…/{sessionId}/subagents/agent-{id}.jsonl` + `agent-{id}.meta.json`
  (`{agentType, description, toolUseId}`); `toolUseId` links the subagent to the
  parent's `Agent`/`Task` tool call.
- Large tool outputs: `…/{sessionId}/tool-results/{id}.txt`, referenced from a
  `<persisted-output>` wrapper in the transcript; loaded on demand.
- Messages chain via `uuid`/`parentUuid`; tool results pair to tool uses via
  `tool_use_id`. The parser tolerates unknown line types (skips them).

### API

- `GET /api/sessions` → session summaries
- `GET /api/sessions/{id}` → reconstructed thread
- `GET /api/sessions/{id}/subagents/{agentId}` → subagent thread
- `GET /api/sessions/{id}/tool-results/{cacheId}` → full persisted output
- `GET /api/sessions/{id}/stream` → SSE (`append`, `tool_result`, `heartbeat`)
- `GET /api/health`

## Running

Install once:

```bash
uv venv --python 3.10 && uv pip install -e ".[dev]"
( cd frontend && npm install )
```

Development (two servers, hot reload):

```bash
./scripts/dev.sh
# open http://127.0.0.1:5173
```

Production-style (single server serving the built SPA), managed via the `muse` CLI:

```bash
( cd frontend && npm run build )
muse start      # foreground; or `scripts/restart.sh` to (re)start in place
# open http://127.0.0.1:8848
```

`muse` is the canonical way to manage the server, so background launches can't pile up:

```bash
muse start      # refuses to start if an instance is already running
muse stop       # stop the running instance (via its pidfile)
muse restart    # stop + start — the way to apply code changes (no --reload in prod)
muse status     # version, uptime, and a STALE warning if the running code is older than the source
```

Only one instance may run at a time (guarded by `~/.muse/muse.pid`); set
`MUSE_SINGLETON=off` to override. `GET /api/version` reports the same info for scripts.

Tests / lint:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check backend
```

### Configuration (env vars)

- `MUSE_CLAUDE_DIR` — Claude dir to read (default `~/.claude`)
- `MUSE_PORT` / `MUSE_HOST`
- `MUSE_POLL_DELAY_MS` — live-tail poll interval (default 500). muse polls rather
  than using inotify, which is robust against the inotify-watch exhaustion common
  on machines actively running Claude Code.
- `MUSE_RUNNING_THRESHOLD_SECONDS` — how recent an mtime counts as "running" (30).

## Future: job queue + tmux injection

The backend is a long-running service by design so a job/worker layer can slot
in without restructuring (see `backend/muse/jobs/__init__.py`):

- `services/events.py` (the pub/sub broker) already streams events to clients;
  a worker will publish job lifecycle events onto a `job:{id}` topic and reuse
  the existing SSE machinery.
- `services/session_service.py` is the only seam routers touch; job methods land
  there, keeping routers thin.
- `main.py`'s lifespan owns long-lived state, so a background worker task and a
  `tmux_adapter` (libtmux `send-keys` into a Claude Code pane) start/stop with
  the broker and tailer registry.
