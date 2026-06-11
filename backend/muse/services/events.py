"""In-process async pub/sub broker.

Today this carries live-tail events (new transcript lines) to SSE subscribers.
Tomorrow the job/worker layer publishes job status onto the *same* broker, so
the streaming plumbing never has to change — only the set of topics grows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class Event:
    topic: str
    type: str  # e.g. "append", "tool_result", "meta", "heartbeat"
    data: Any


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, topic: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.setdefault(topic, set()).add(queue)
        return queue

    async def unsubscribe(self, topic: str, queue: asyncio.Queue[Event]) -> None:
        async with self._lock:
            subs = self._subscribers.get(topic)
            if subs:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(topic, None)

    async def subscriber_count(self, topic: str) -> int:
        async with self._lock:
            return len(self._subscribers.get(topic, ()))

    def publish(self, event: Event) -> None:
        """Fan an event out to all subscribers of its topic (non-blocking)."""
        for queue in list(self._subscribers.get(event.topic, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer; drop rather than stall the producer
