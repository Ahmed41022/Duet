"""Bounded, session-aware server-sent events."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_EVENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


class EventHub:
    """Publish events to connected browsers from the server event loop."""

    def __init__(self) -> None:
        self._listeners: dict[asyncio.Queue[bytes | None], str] = {}
        self._closed = False

    @asynccontextmanager
    async def subscribe(
        self, session_id: str
    ) -> AsyncIterator[asyncio.Queue[bytes | None]]:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)
        if self._closed:
            queue.put_nowait(None)
        else:
            self._listeners[queue] = session_id
        try:
            yield queue
        finally:
            self._listeners.pop(queue, None)

    def publish(
        self, event: str, data: object, *, session_id: str | None = None
    ) -> None:
        if not _EVENT_NAME.fullmatch(event):
            raise ValueError("Invalid SSE event name")
        payload = json.dumps(
            data, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )
        frame = f"event: {event}\ndata: {payload}\n\n".encode("ascii")
        if len(frame) > 64 * 1024:
            raise ValueError("SSE event exceeds 64 KiB")
        for queue, listener_id in tuple(self._listeners.items()):
            if session_id is not None and session_id != listener_id:
                continue
            if queue.full():
                self._disconnect(queue)
            else:
                queue.put_nowait(frame)

    def close(self) -> None:
        self._closed = True
        for queue in tuple(self._listeners):
            self._disconnect(queue)

    def _disconnect(self, queue: asyncio.Queue[bytes | None]) -> None:
        self._listeners.pop(queue, None)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)
