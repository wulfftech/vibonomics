"""In-process event bus feeding the ``/api/events`` server-sent-events stream.

Producers may run on worker threads (sync API handlers), so ``publish`` is thread-safe and hops
onto the event loop captured at startup.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

_subscribers: set[asyncio.Queue[str]] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _deliver(message: str) -> None:
    for queue in list(_subscribers):
        if queue.qsize() < 500:
            queue.put_nowait(message)


def publish(event: str, data: dict[str, Any] | None = None) -> None:
    message = json.dumps({"event": event, "data": data or {}}, default=str)
    if _loop is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _deliver(message)
    else:
        _loop.call_soon_threadsafe(_deliver, message)


async def stream() -> AsyncIterator[str]:
    queue: asyncio.Queue[str] = asyncio.Queue()
    _subscribers.add(queue)
    try:
        yield "retry: 5000\n\n"
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=20)
                yield f"data: {message}\n\n"
            except TimeoutError:
                yield ": keepalive\n\n"
    finally:
        _subscribers.discard(queue)
