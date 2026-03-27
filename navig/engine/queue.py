"""
navig.engine.queue — Lane-based asyncio command queue.

Each *lane* is an independent serialized execution channel.  Commands in the
same lane run one at a time; different lanes run concurrently.

Key concepts
------------
- **Lane** (str): logical namespace.  The default lane is "main".
- **TaskHandle**: cancellable reference returned from :meth:`CommandQueue.enqueue`.
- **LaneClearedError**: raised inside a pending coroutine when its lane is cleared.

Usage
-----
    q = CommandQueue(max_workers_per_lane=1)

    handle = await q.enqueue("main", some_coroutine())
    result = await handle.wait()          # raises LaneClearedError if cleared

    await q.clear_lane("main")            # cancel all pending tasks in "main"
    await q.drain()                        # wait for all lanes to finish
    await q.shutdown()                     # cancel everything and clean up
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LaneClearedError(RuntimeError):
    """Raised when a queued task is cancelled because its lane was cleared."""

    def __init__(self, lane: str) -> None:
        super().__init__(f"Lane '{lane}' was cleared before task completed")
        self.lane = lane


class QueueShutdownError(RuntimeError):
    """Raised when a task is enqueued into a shut-down queue."""


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class TaskHandle:
    """Opaque handle returned to callers of :meth:`CommandQueue.enqueue`.

    Attributes
    ----------
    task_id:  unique identifier (UUID4)
    lane:     the lane this task belongs to
    """

    task_id: str
    lane: str
    _future: asyncio.Future = field(repr=False)
    _async_task: asyncio.Task | None = field(default=None, repr=False)
    _state: TaskState = field(default=TaskState.PENDING, repr=False)
    _enqueued_at: float = field(default_factory=time.monotonic, repr=False)

    # ------------------------------------------------------------------

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def done(self) -> bool:
        return self._state in (TaskState.DONE, TaskState.CANCELLED, TaskState.ERROR)

    async def wait(self) -> Any:
        """Await the result of this task.

        Raises
        ------
        LaneClearedError  if the lane was cleared before this task finished
        Exception          any exception raised by the wrapped coroutine
        """
        return await self._future

    def cancel(self, reason: str = "cancelled by caller") -> bool:
        """Request cancellation of the underlying asyncio.Task.

        Returns True if the cancellation request was delivered.
        """
        if self._async_task and not self._async_task.done():
            self._async_task.cancel(reason)
            self._state = TaskState.CANCELLED
            return True
        return False

    def __repr__(self) -> str:  # pragma: no cover
        return f"TaskHandle(id={self.task_id[:8]}… lane={self.lane!r} state={self._state})"


# ---------------------------------------------------------------------------
# Internal lane worker
# ---------------------------------------------------------------------------


@dataclass
class _LaneState:
    lane: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker_task: asyncio.Task | None = None
    active_handles: set[str] = field(default_factory=set)

    async def _worker(self) -> None:  # pragma: no cover
        """Drain the queue serially until None sentinel is received."""
        while True:
            entry: tuple | None = await self.queue.get()
            if entry is None:  # shutdown sentinel
                self.queue.task_done()
                break
            handle, coro, timeout = entry
            handle._state = TaskState.RUNNING
            try:
                if timeout and timeout > 0:
                    wrapped = asyncio.wait_for(coro, timeout=timeout)
                else:
                    wrapped = coro
                result = await wrapped
                handle._state = TaskState.DONE
                if not handle._future.done():
                    handle._future.set_result(result)
            except asyncio.CancelledError:
                handle._state = TaskState.CANCELLED
                if not handle._future.done():
                    handle._future.set_exception(LaneClearedError(handle.lane))
                raise
            except Exception as exc:
                handle._state = TaskState.ERROR
                if not handle._future.done():
                    handle._future.set_exception(exc)
            finally:
                self.active_handles.discard(handle.task_id)
                self.queue.task_done()


# ---------------------------------------------------------------------------
# CommandQueue
# ---------------------------------------------------------------------------


class CommandQueue:
    """Per-lane serialized command queue backed by asyncio.

    Parameters
    ----------
    max_workers_per_lane:
        How many tasks may *run concurrently within a single lane*.
        Default 1 (fully serialized per lane).
    default_timeout:
        Per-task execution timeout in seconds.  0 = no timeout.
    """

    def __init__(
        self,
        *,
        max_workers_per_lane: int = 1,
        default_timeout: float = 120.0,
    ) -> None:
        if max_workers_per_lane < 1:
            raise ValueError("max_workers_per_lane must be >= 1")
        self._max_workers = max_workers_per_lane
        self._default_timeout = default_timeout
        self._lanes: dict[str, _LaneState] = {}
        self._shutdown = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_lane(self, lane: str) -> _LaneState:
        if lane not in self._lanes:
            ls = _LaneState(lane=lane)
            self._lanes[lane] = ls
            task = asyncio.create_task(ls._worker())
            ls.worker_task = task
        return self._lanes[lane]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        lane: str,
        coro: Awaitable[Any],
        *,
        timeout: float | None = None,
        task_id: str | None = None,
    ) -> TaskHandle:
        """Submit *coro* to *lane* and return a :class:`TaskHandle`.

        The coroutine will start only when all previously enqueued tasks in
        the same lane have completed.

        Parameters
        ----------
        lane:     lane name; created on first use
        coro:     awaitable to execute
        timeout:  override per-task timeout (seconds); None = use default
        task_id:  custom task ID; auto-generated UUID4 if not supplied
        """
        if self._shutdown:
            raise QueueShutdownError("CommandQueue has been shut down")

        effective_timeout = timeout if timeout is not None else self._default_timeout

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        handle = TaskHandle(
            task_id=task_id or str(uuid.uuid4()),
            lane=lane,
            _future=future,
        )

        ls = self._ensure_lane(lane)
        ls.active_handles.add(handle.task_id)
        # Store (handle, raw_coro, timeout) — wrapping with wait_for is deferred
        # to the worker so that unawaited coroutines can be cleanly closed on
        # lane clear without "coroutine was never awaited" warnings.
        await ls.queue.put((handle, coro, effective_timeout))

        logger.debug(
            "CommandQueue.enqueue: task=%s lane=%s queue_depth=%d",
            handle.task_id[:8],
            lane,
            ls.queue.qsize(),
        )
        return handle

    async def clear_lane(self, lane: str) -> int:
        """Cancel all pending and running tasks in *lane*.

        Returns the number of tasks that were cancelled.
        """
        if lane not in self._lanes:
            return 0

        ls = self._lanes[lane]
        cancelled = 0

        # Drain the queue and cancel queued items
        pending: list[tuple] = []
        while not ls.queue.empty():
            try:
                entry = ls.queue.get_nowait()
                if entry is not None:
                    pending.append(entry)
                    ls.queue.task_done()
            except asyncio.QueueEmpty:
                break

        for handle, coro, _ in pending:
            # Close unawaited coroutines to suppress "was never awaited" warnings
            if hasattr(coro, "close"):
                coro.close()
            handle._state = TaskState.CANCELLED
            if not handle._future.done():
                handle._future.set_exception(LaneClearedError(lane))
            ls.active_handles.discard(handle.task_id)
            cancelled += 1

        # Cancel the worker itself (aborts the currently running task if any)
        if ls.worker_task and not ls.worker_task.done():
            ls.worker_task.cancel()
            cancelled += 1

        # Remove lane so future enqueues start a fresh worker
        del self._lanes[lane]

        logger.debug("CommandQueue.clear_lane: lane=%s cancelled=%d", lane, cancelled)
        return cancelled

    async def drain(self, lane: str | None = None, *, timeout: float = 30.0) -> None:
        """Wait for *lane* (or all lanes) to finish processing.

        Raises asyncio.TimeoutError if not drained within *timeout* seconds.
        """
        targets = (
            [self._lanes[lane]] if lane and lane in self._lanes else list(self._lanes.values())
        )
        waits = [ls.queue.join() for ls in targets]
        if not waits:
            return
        try:
            await asyncio.wait_for(asyncio.gather(*waits), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("CommandQueue.drain: timed out after %.1fs", timeout)
            raise

    async def shutdown(self) -> None:
        """Cancel all lanes and prevent further enqueues."""
        self._shutdown = True
        for lane in list(self._lanes.keys()):
            await self.clear_lane(lane)
        logger.debug("CommandQueue: shutdown complete")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return a snapshot of all lanes and their depths."""
        return {
            lane: {
                "queue_depth": ls.queue.qsize(),
                "active": len(ls.active_handles),
                "worker_alive": bool(ls.worker_task and not ls.worker_task.done()),
            }
            for lane, ls in self._lanes.items()
        }

    @property
    def lane_names(self) -> list[str]:
        return list(self._lanes.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CommandQueue(lanes={len(self._lanes)} "
            f"max_workers={self._max_workers} "
            f"shutdown={self._shutdown})"
        )
