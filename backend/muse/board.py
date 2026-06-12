"""Mission-control board: a demand-driven ticker that builds a lightweight
per-session snapshot every few seconds and publishes card deltas to the broker.

Design constraints (why this looks the way it does):
- The ticker NEVER parses a whole transcript. Live sessions append constantly,
  which defeats the (mtime,size)-keyed parse caches — a full parse per tick
  would pin a core. Activity lines and live health come from
  `incremental.new_objects` on appended bytes only, primed from the last 64KB.
- It runs ONLY while someone is watching (SSE subscriber refcount, or a recent
  GET /api/board). The alerts tick keeps running unwatched; the board costs
  zero when the page is closed.
- Token/cost + context aggregation uses `usage_cache.board_rollup`, whose
  per-file mtime-keyed cache re-sums only files that actually changed.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from . import discovery, usage_cache
from .autopilot import sessions as live_discovery
from .incremental import new_objects
from .models import BoardActivity, BoardCard, BoardSnapshot
from .paths import SessionPaths
from .patterns import RollingHealth
from .services.events import Event

TICK_SECONDS = 3.0
_DEMAND_WINDOW = 15.0  # a GET keeps the ticker alive this long
_PRIME_BYTES = 64 * 1024  # cold-start tail read; never the whole file
_SCOPE_SECONDS = 48 * 3600  # stopped sessions older than this stay off the board
_ACTIVITY_CHARS = 160


def _activity_from_obj(obj: dict) -> Optional[BoardActivity]:
    """Extract a one-line activity from one raw transcript object, or None."""
    ts = None
    raw_ts = obj.get("timestamp")
    if isinstance(raw_ts, str):
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    typ = obj.get("type")
    if obj.get("isApiErrorMessage"):
        return BoardActivity(
            kind="error", text=str(obj.get("content") or "API error")[:_ACTIVITY_CHARS], ts=ts
        )
    if typ == "system" and obj.get("level") == "error":
        return BoardActivity(
            kind="error", text=str(obj.get("content") or "error")[:_ACTIVITY_CHARS], ts=ts
        )
    if typ == "assistant":
        msg = obj.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            return None
        # Prefer the last tool call (it names what's happening); else last text.
        tool: Optional[BoardActivity] = None
        text: Optional[BoardActivity] = None
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                inp = b.get("input") or {}
                label = ""
                for key in ("command", "file_path", "path", "pattern", "query",
                            "description", "prompt", "url"):
                    v = inp.get(key)
                    if isinstance(v, str) and v.strip():
                        label = v.strip().splitlines()[0]
                        break
                tool = BoardActivity(
                    kind="tool_call",
                    tool=b.get("name"),
                    text=label[:_ACTIVITY_CHARS],
                    ts=ts,
                )
            elif b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                text = BoardActivity(
                    kind="assistant_text",
                    text=b["text"].strip().splitlines()[0][:_ACTIVITY_CHARS],
                    ts=ts,
                )
        return tool or text
    if typ == "user":
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return BoardActivity(
                kind="user", text=content.strip().splitlines()[0][:_ACTIVITY_CHARS], ts=ts
            )
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                    text = b.get("content")
                    if isinstance(text, list):
                        text = " ".join(
                            str(p.get("text", "")) for p in text if isinstance(p, dict)
                        )
                    return BoardActivity(
                        kind="error", text=str(text or "tool error")[:_ACTIVITY_CHARS], ts=ts
                    )
    return None


class BoardTicker:
    def __init__(self, service, broker) -> None:
        self.service = service
        self.broker = broker
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._subscribers = 0
        self._last_demand = 0.0
        self._lock = asyncio.Lock()
        # Per-session incremental state (claude only; other providers have no
        # local jsonl we tail this way).
        self._offsets: dict[str, int] = {}
        self._activity: dict[str, BoardActivity] = {}
        self._rolling: dict[str, RollingHealth] = {}
        self._prev_cards: dict[str, str] = {}  # sid -> serialized card (diffing)
        self.latest: Optional[BoardSnapshot] = None

    # --- demand management -----------------------------------------------------
    async def acquire(self) -> None:
        async with self._lock:
            self._subscribers += 1
            await self._ensure_running()

    async def release(self) -> None:
        async with self._lock:
            self._subscribers = max(0, self._subscribers - 1)

    async def get_snapshot(self) -> BoardSnapshot:
        """Latest snapshot (bootstrap + polling fallback). Marks demand so the
        ticker keeps running ~15s past the last poll; builds cold if needed."""
        self._last_demand = time.monotonic()
        async with self._lock:
            await self._ensure_running()
        if self.latest is None:
            cards = await asyncio.to_thread(self._build_cards)
            self.latest = BoardSnapshot(
                generated_at=datetime.now(timezone.utc), cards=cards
            )
            self._prev_cards = {c.session_id: c.model_dump_json() for c in cards}
        return self.latest

    def _demand(self) -> bool:
        return (
            self._subscribers > 0
            or (time.monotonic() - self._last_demand) < _DEMAND_WINDOW
        )

    async def _ensure_running(self) -> None:
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

    # --- loop --------------------------------------------------------------------
    async def _run(self) -> None:
        while not self._stop.is_set():
            if not self._demand():
                break  # idle out; next acquire()/get_snapshot() restarts us
            try:
                await self._tick()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        cards = await asyncio.to_thread(self._build_cards)
        snapshot = BoardSnapshot(generated_at=datetime.now(timezone.utc), cards=cards)
        self.latest = snapshot
        new_map = {c.session_id: c.model_dump_json() for c in cards}
        updated = [
            c for c in cards
            if self._prev_cards.get(c.session_id) != new_map[c.session_id]
        ]
        removed = [sid for sid in self._prev_cards if sid not in new_map]
        self._prev_cards = new_map
        if updated or removed:
            self.broker.publish(
                Event(
                    topic="board",
                    type="cards",
                    data={
                        "updated": [c.model_dump(mode="json") for c in updated],
                        "removed": removed,
                    },
                )
            )

    # --- snapshot construction (runs in a worker thread) ---------------------------
    def _build_cards(self) -> list[BoardCard]:
        now = time.time()
        summaries = self.service.list_sessions()  # SWR-cached
        live = {s.session_id: s for s in live_discovery.discover()}
        # O(#files): per-file aggregates re-sum only files whose mtime changed.
        aggs, pcts = usage_cache.board_rollup()

        cards: list[BoardCard] = []
        seen: set[str] = set()
        for s in summaries:
            mtime = s.mtime.timestamp()
            ls = live.get(s.session_id)
            if ls is None and (now - mtime) > _SCOPE_SECONDS:
                continue  # old stopped sessions live on the Sessions page
            seen.add(s.session_id)
            # Re-derive the time-dependent state per tick (the cached summary's
            # state can be up to the list TTL stale).
            state = s.state
            fresh = s.model_copy()
            discovery.apply_state(fresh, mtime)
            state = fresh.state

            activity, rolling = self._tail(s)
            tokens, cost = aggs.get(s.session_id, (0, 0.0))
            health = s.health
            flags: list[str] = []
            if rolling is not None:
                live_score, live_flags = rolling.score()
                flags = live_flags
                order = {"ok": 0, "warn": 1, "bad": 2, None: -1}
                if order.get(live_score, 0) > order.get(health, -1):
                    health = live_score  # worst-of(snapshot, rolling)

            cards.append(
                BoardCard(
                    session_id=s.session_id,
                    provider=s.provider,
                    title=s.title,
                    project_cwd=s.project_cwd,
                    state=state,
                    live_status=ls.status if ls else None,
                    waiting_for=ls.waiting_for if ls else None,
                    has_pane=bool(ls and ls.pane_id),
                    pane_id=ls.pane_id if ls else None,
                    context_pct=pcts.get(s.session_id),
                    total_tokens=tokens or s.total_tokens,
                    cost_usd=round(cost, 4),
                    health=health,
                    health_flags=flags,
                    last_activity=activity,
                    mtime=s.mtime,
                    model=s.model,
                    git_branch=s.git_branch,
                )
            )
        # Forget incremental state for sessions that left the board.
        for sid in list(self._offsets):
            if sid not in seen:
                self._offsets.pop(sid, None)
                self._activity.pop(sid, None)
                self._rolling.pop(sid, None)

        order = {"waiting": 0, "live": 1, "stopped": 2}
        cards.sort(key=lambda c: (order.get(c.state, 3), -c.mtime.timestamp()))
        return cards

    def _tail(self, summary) -> tuple[Optional[BoardActivity], Optional[RollingHealth]]:
        """Incremental activity + rolling-health update for one claude session.
        Reads ONLY bytes appended since the previous tick (64KB tail to prime)."""
        if summary.provider != "claude":
            return None, None
        sid = summary.session_id
        path = SessionPaths(project_dir=summary.project_dir, session_id=sid).jsonl
        try:
            size = path.stat().st_size
        except OSError:
            return self._activity.get(sid), self._rolling.get(sid)
        prev = self._offsets.get(sid)
        if prev is None or prev > size:  # new to the board, or file was rewritten
            prev = max(0, size - _PRIME_BYTES)
            self._rolling[sid] = RollingHealth()
            if prev > 0:
                # Skip the first (likely partial) line of the tail window.
                try:
                    with path.open("rb") as fh:
                        fh.seek(prev)
                        head = fh.readline()
                    prev += len(head)
                except OSError:
                    pass
        objs, new_off = new_objects(path, prev)
        self._offsets[sid] = new_off
        rolling = self._rolling.setdefault(sid, RollingHealth())
        for obj in objs:
            rolling.feed(obj)
            act = _activity_from_obj(obj)
            if act is not None:
                self._activity[sid] = act
        return self._activity.get(sid), rolling
