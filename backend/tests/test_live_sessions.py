from pathlib import Path
from types import SimpleNamespace

from muse.autopilot import sessions as live_sessions


def _mkdb(path: Path, sql: str) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def test_discover_codex_matches_pane_to_thread_id(monkeypatch, tmp_path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    state = codex_dir / "state_5.sqlite"
    logs = codex_dir / "logs_2.sqlite"
    _mkdb(
        state,
        """
        CREATE TABLE threads (
          id TEXT PRIMARY KEY,
          rollout_path TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          source TEXT NOT NULL,
          model_provider TEXT NOT NULL,
          cwd TEXT NOT NULL,
          title TEXT NOT NULL,
          sandbox_policy TEXT NOT NULL,
          approval_mode TEXT NOT NULL,
          tokens_used INTEGER NOT NULL DEFAULT 0,
          has_user_event INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0,
          archived_at INTEGER,
          git_sha TEXT,
          git_branch TEXT,
          git_origin_url TEXT,
          cli_version TEXT NOT NULL DEFAULT '',
          first_user_message TEXT NOT NULL DEFAULT '',
          agent_nickname TEXT,
          agent_role TEXT,
          memory_mode TEXT NOT NULL DEFAULT 'enabled',
          model TEXT,
          reasoning_effort TEXT,
          agent_path TEXT,
          created_at_ms INTEGER,
          updated_at_ms INTEGER,
          thread_source TEXT,
          preview TEXT NOT NULL DEFAULT '',
          recency_at INTEGER NOT NULL DEFAULT 0,
          recency_at_ms INTEGER NOT NULL DEFAULT 0,
          history_mode TEXT NOT NULL DEFAULT 'legacy'
        );
        INSERT INTO threads (
          id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
          sandbox_policy, approval_mode, created_at_ms, updated_at_ms, cli_version
        ) VALUES (
          '019f44ea-d2c3-7df1-9d1c-b240b4e748d8',
          '/tmp/rollout.jsonl', 0, 0, 'cli', 'openai', '/home/luke/workspace/muse',
          'muse session', 'workspace-write', 'never', 1783567667908, 1783570008098, '0.143.0'
        );
        """,
    )
    _mkdb(
        logs,
        """
        CREATE TABLE logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts INTEGER NOT NULL,
          ts_nanos INTEGER NOT NULL,
          level TEXT NOT NULL,
          target TEXT NOT NULL,
          feedback_log_body TEXT,
          module_path TEXT,
          file TEXT,
          line INTEGER,
          thread_id TEXT,
          process_uuid TEXT,
          estimated_bytes INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO logs (ts, ts_nanos, level, target, thread_id, process_uuid)
        VALUES (1783570008, 0, 'INFO', 'codex_core::turn', '019f44ea-d2c3-7df1-9d1c-b240b4e748d8',
                'pid:2525803:ab823cb6-2407-4060-9358-6636ba8568fd');
        """,
    )

    monkeypatch.setattr(
        live_sessions,
        "get_settings",
        lambda: SimpleNamespace(claude_dir=tmp_path / "claude", codex_dir=codex_dir),
    )
    monkeypatch.setattr(
        live_sessions.tmux,
        "list_panes",
        lambda: [
            {
                "pane_id": "%163",
                "pane_pid": 2525610,
                "cmd": "node",
                "cwd": "/home/luke/workspace/muse",
            }
        ],
    )
    monkeypatch.setattr(
        live_sessions,
        "_proc_snapshot",
        lambda: {
            2525610: {"ppid": 1, "comm": "node", "cmdline": "node /home/luke/.npm-global/bin/codex"},
            2525792: {"ppid": 2525610, "comm": "node", "cmdline": "node /home/luke/.npm-global/bin/codex"},
            2525803: {"ppid": 2525792, "comm": "codex", "cmdline": "/vendor/bin/codex"},
        },
    )

    got = live_sessions.discover()
    codex = next((s for s in got if s.session_id.startswith("codex:")), None)
    assert codex is not None
    assert codex.session_id == "codex:019f44ea-d2c3-7df1-9d1c-b240b4e748d8"
    assert codex.pane_id == "%163"
    assert codex.cwd == "/home/luke/workspace/muse"
    assert codex.version == "0.143.0"
    assert codex.status == "shell"
