"""Thread-safe fan-out from worker threads to asyncio subscribers (WebSockets)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

_Subscriber = tuple[asyncio.AbstractEventLoop, asyncio.Queue[Any]]


def _put_latest(queue: asyncio.Queue[Any], message: Any) -> None:
    # A slow client loses the oldest messages instead of slowing the producer.
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(message)


class Hub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[_Subscriber]] = {}

    def publish(self, topic: str, message: Any) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(topic, ()))
        for loop, queue in subscribers:
            with contextlib.suppress(RuntimeError):  # loop already closed
                loop.call_soon_threadsafe(_put_latest, queue, message)

    def subscriber_count(self, topic: str) -> int:
        with self._lock:
            return len(self._subscribers.get(topic, ()))

    @asynccontextmanager
    async def subscribe(self, topic: str, maxsize: int = 32) -> AsyncIterator[asyncio.Queue[Any]]:
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        entry = (asyncio.get_running_loop(), queue)
        with self._lock:
            self._subscribers.setdefault(topic, []).append(entry)
        try:
            yield queue
        finally:
            with self._lock:
                self._subscribers[topic].remove(entry)
