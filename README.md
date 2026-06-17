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
- `MUSE_AI_MODEL` / `MUSE_AI_TIMEOUT_SECONDS` — the headless `claude -p` model
  (default `sonnet`) and per-job timeout used by Ask muse, summaries, digests,
  draft-reply, diagnose, and triage.
- `MUSE_AI_AUTO_DIGEST` — opt in (`1`) to auto-generate the daily journal digest
  and the Monday weekly retro.
- `MUSE_AI_DAILY_BUDGET_USD` — cap on AI spend from autopilot's `ai` idle mode
  (default `2.0`; `0` disables that mode — human-initiated AI still works).
- `MUSE_AUTH_TOKEN` / `MUSE_AUTH_ALLOW_LOOPBACK` / `MUSE_PUBLIC_URL` — see below.

## Remote access (tailscale / LAN)

muse is local-first: bound to `127.0.0.1` with no auth, nothing changes. To reach
it from your phone over tailscale, bind publicly and set a token:

```bash
MUSE_HOST=0.0.0.0 \
MUSE_PUBLIC_URL=http://<your-tailnet-host>:8848 \
MUSE_AUTH_TOKEN=$(openssl rand -base64 24) \
muse restart
# (omit MUSE_AUTH_TOKEN and muse generates ~/.muse/auth_token on first non-loopback bind)
```

- `/api/*` and `/mcp` now require the token (Bearer header or the cookie set by
  the in-app login prompt); the SPA shell stays public. **Loopback always
  bypasses** so your local UI, scripts, and local Claude Code MCP keep working
  untouched — set `MUSE_AUTH_ALLOW_LOOPBACK=0` to require the token even locally.
- `MUSE_PUBLIC_URL` makes ntfy notification taps and MCP-cited links open the
  reachable address instead of `127.0.0.1`.
- Remote Claude Code MCP: `claude mcp add --transport http muse <url>/mcp/ --header "Authorization: Bearer <token>"`.
- Within a tailnet the http cookie is fine; if you expose muse beyond it, front
  it with TLS (the cookie auto-upgrades to `Secure` over https).
- Installs as a PWA (Add to Home Screen); the board, session list, journal, ask,
  and a conversation-only viewer are phone-sized.

## AI layer

muse calls Claude itself via the headless `claude -p` CLI (your Max-plan auth;
no API key), one job at a time on a background worker:

- **Ask muse** — a question across your whole history, answered with deep links
  into the cited sessions.
- **Mission control** (the board) — AI-drafted replies you edit and send,
  stuck-session diagnosis, and one-pass triage of everything needing attention.
- **Autopilot `ai` mode** — drafts and (re-checking idleness + a daily budget)
  types the next reply into a live tmux pane; never answers permission prompts.
- **Digests / retros / summaries** — journal and investigation entries.

`services/session_service.py` is the only seam routers touch; the worker and the
tmux transport start/stop with the app lifespan in `main.py`.
