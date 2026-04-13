"""In-memory event bus for SSE push notifications."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class SSEEvent:
    type: str
    resource_id: str
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    metadata: dict[str, object] | None = None

    def to_sse(self) -> str:
        data: dict = {"type": self.type, "resource_id": self.resource_id, "timestamp": self.timestamp}
        if self.metadata:
            data.update(self.metadata)
        return f"event: {self.type}\ndata: {json.dumps(data)}\n\n"


class EventBus:
    """Broadcast SSE events to all connected subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[SSEEvent]] = []
        self._internal_callbacks: list[Callable[[SSEEvent], Awaitable[None]]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop for use in sync-context fallback."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[SSEEvent]:
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[SSEEvent]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    def subscribe_internal(self, callback: Callable[[SSEEvent], Awaitable[None]]) -> None:
        self._internal_callbacks.append(callback)

    def unsubscribe_internal(self, callback: Callable[[SSEEvent], Awaitable[None]]) -> None:
        try:
            self._internal_callbacks.remove(callback)
        except ValueError:
            pass

    def publish(self, event: SSEEvent) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        for callback in self._internal_callbacks:
            try:
                asyncio.get_running_loop().create_task(callback(event))
            except RuntimeError:
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(self._loop.create_task, callback(event))
