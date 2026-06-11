"""Live tailing of session transcripts.

Watches a session's .jsonl (and its subagents/ dir) for appends, parses only the
newly-written complete lines, and publishes incremental events to the broker.

Reference-counted per session: a tailer task starts when the first SSE client
subscribes and stops when the last disconnects.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from watchfiles import awatch

from .config import get_settings
from .models import ThreadItem
from .parser import extract_tool_results, parse_line
from .paths import SessionPaths, find_session
from .services.events import Event, EventBroker


class _FileTail:
    """Tracks a byte offset into one file and yields new complete JSON lines."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # Start at end of file: live tailing only streams *new* activity; the
        # initial state is delivered by the REST thread load.
        self.offset = path.stat().st_size if path.is_file() else 0
        self._buffer = ""

    def read_new(self) -> list[dict]:
        if not self.path.is_file():
            return []
        size = self.path.stat().st_size
        if size < self.offset:  # file truncated/rotated
            self.offset = 0
            self._buffer = ""
        objs: list[dict] = []
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
            self.offset = fh.tell()
        self._buffer += chunk
        # Only consume up to the last newline; hold any partial trailing line.
        if "\n" not in self._buffer:
            return objs
        complete, self._buffer = self._buffer.rsplit("\n", 1)
        import json

        for line in complete.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                objs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return objs


class SessionTailer:
    def __init__(self, broker: EventBroker, paths: SessionPaths) -> None:
        self.broker = broker
        self.paths = paths
        self.topic = paths.session_id
        self._tails: dict[Path, _FileTail] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._stop.set()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _watch_targets(self) -> list[Path]:
        targets = [self.paths.jsonl]
        if self.paths.subagents_dir.is_dir():
            targets.append(self.paths.subagents_dir)
        return targets

    def _emit_for_file(self, path: Path) -> None:
        tail = self._tails.get(path)
        if tail is None:
            tail = _FileTail(path)
            self._tails[path] = tail
        new_items: list[ThreadItem] = []
        for obj in tail.read_new():
            for res in extract_tool_results(obj):
                if res.tool_use_id:
                    self.broker.publish(
                        Event(self.topic, "tool_result", res.model_dump(mode="json"))
                    )
            item = parse_line(obj)
            if item is not None:
                new_items.append(item)
        if new_items:
            self.broker.publish(
                Event(
                    self.topic,
                    "append",
                    {"items": [i.model_dump(mode="json") for i in new_items]},
                )
            )

    async def _run(self) -> None:
        # Prime offsets for files that already exist.
        for path in [self.paths.jsonl, *self._existing_subagent_files()]:
            if path.is_file():
                self._tails.setdefault(path, _FileTail(path))
        poll_delay = get_settings().poll_delay_ms
        try:
            async for changes in awatch(
                *self._watch_targets(),
                stop_event=self._stop,
                force_polling=True,
                poll_delay_ms=poll_delay,
            ):
                touched = {Path(p) for _, p in changes}
                for path in touched:
                    if path.suffix == ".jsonl":
                        self._emit_for_file(path)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let the watcher crash take down the request lifecycle.
            return

    def _existing_subagent_files(self) -> list[Path]:
        if not self.paths.subagents_dir.is_dir():
            return []
        return list(self.paths.subagents_dir.glob("*.jsonl"))


class TailerRegistry:
    """Reference-counted registry of per-session tailers."""

    def __init__(self, broker: EventBroker) -> None:
        self.broker = broker
        self._tailers: dict[str, SessionTailer] = {}
        self._refs: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, session_id: str) -> bool:
        paths = find_session(session_id)
        if paths is None:
            return False
        async with self._lock:
            tailer = self._tailers.get(session_id)
            if tailer is None:
                tailer = SessionTailer(self.broker, paths)
                self._tailers[session_id] = tailer
                tailer.start()
            self._refs[session_id] = self._refs.get(session_id, 0) + 1
        return True

    async def release(self, session_id: str) -> None:
        async with self._lock:
            self._refs[session_id] = self._refs.get(session_id, 1) - 1
            if self._refs[session_id] <= 0:
                self._refs.pop(session_id, None)
                tailer = self._tailers.pop(session_id, None)
                if tailer:
                    await tailer.stop()

    def watching(self) -> list[str]:
        return list(self._tailers.keys())

    async def stop_all(self) -> None:
        async with self._lock:
            for tailer in self._tailers.values():
                await tailer.stop()
            self._tailers.clear()
            self._refs.clear()
