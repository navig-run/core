"""Per-key async task serialization with cross-key concurrency.

Tasks for the **same key** (e.g. the same Telegram chat_id) are chained so
they execute one at a time, preventing interleaved handler coroutines and
out-of-order replies.

Tasks for **different keys** run fully concurrently — no global lock.

Usage
-----
from navig.gateway.keyed_queue import KeyedQueue

class MyChannel:
    def __init__(self):
        self._queue = KeyedQueue()

    async def on_message(self, chat_id: int, text: str) -> None:
        await self._queue.enqueue(str(chat_id), self._process(chat_id, text))

    async def _process(self, chat_id: int, text: str) -> None:
        ...
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")


class KeyedQueue:
    """Serialises async tasks per key; different keys run concurrently.

    Internally maintains a ``tails`` dict that maps each active key to the
    last-enqueued ``asyncio.Task``. New tasks for the same key are chained
    after the current tail.  The tail entry is removed once the last task for
    that key completes, keeping the dict bounded.
    """

    def __init__(self) -> None:
        self._tails: dict[str, asyncio.Task[object]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, key: str, coro: Awaitable[T]) -> asyncio.Task[T]:
        """Schedule *coro* to run after all previous tasks for *key*.

        Returns the new ``asyncio.Task`` immediately (not awaitable here —
        the caller can await it if they need the result, or fire-and-forget).
        """
        return _enqueue_keyed_task(self._tails, key, coro)

    @property
    def active_keys(self) -> frozenset[str]:
        """Keys that currently have a pending or running task."""
        return frozenset(self._tails)

    def __len__(self) -> int:  # pragma: no cover
        return len(self._tails)


# ---------------------------------------------------------------------------
# Low-level primitive (also importable standalone for lightweight use)
# ---------------------------------------------------------------------------

def _enqueue_keyed_task(
    tails: dict[str, asyncio.Task[object]],
    key: str,
    coro: Awaitable[T],
) -> asyncio.Task[T]:
    """Chain *coro* after the existing tail for *key* (if any).

    The implementation is ~15 lines of pure asyncio — no deps, no locks.
    """
    loop = asyncio.get_event_loop()
    prev_tail: asyncio.Task[object] | None = tails.get(key)

    async def _runner() -> T:
        if prev_tail is not None and not prev_tail.done():
            try:
                await asyncio.shield(prev_tail)
            except Exception:  # noqa: BLE001 — absorb predecessor errors
                pass
        return await coro  # type: ignore[return-value]

    task: asyncio.Task[T] = loop.create_task(_runner())

    # Register tail; clean up when task finishes
    tails[key] = task  # type: ignore[assignment]

    def _cleanup(t: asyncio.Task[T]) -> None:
        if tails.get(key) is t:
            del tails[key]
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            _log.debug("keyed_queue[%s] task raised %r", key, exc)

    task.add_done_callback(_cleanup)
    return task
