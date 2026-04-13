"""Task worker — dequeues and executes tasks from a TaskQueue."""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from navig.debug_logger import get_debug_logger

from .queue import Task, TaskQueue, TaskStatus

logger = get_debug_logger()


@dataclass
class WorkerConfig:
    """Configuration for a TaskWorker instance."""

    max_concurrent: int = 5
    """Maximum number of tasks to execute concurrently."""

    poll_interval: float = 1.0
    """Polling interval (seconds) when the queue is empty."""

    shutdown_timeout: float = 30.0
    """Grace period (seconds) to wait for running tasks on stop()."""

    default_timeout: float = 300.0
    """Per-task execution timeout (seconds) when Task.timeout is not set."""


class TaskWorker:
    """Concurrent task worker that drains a :class:`~navig.tasks.queue.TaskQueue`.

    Supports:
    - Bounded concurrency via an asyncio semaphore.
    - Per-task and global default timeouts.
    - Sync and async handler functions.
    - Graceful shutdown with configurable drain timeout.
    - Inline execution via :meth:`execute_now` (bypasses the queue).

    Example::

        queue = TaskQueue()
        worker = TaskWorker(queue)

        @worker.handler("backup_database")
        async def backup(params: dict) -> dict:
            host = params["host"]
            return {"bytes": 1_200_000}

        await worker.start()
        await queue.add(Task(name="backup", handler="backup_database", params={"host": "prod"}))
        await worker.stop()
    """

    def __init__(
        self,
        queue: TaskQueue,
        config: WorkerConfig | None = None,
    ) -> None:
        self.queue = queue
        self.config = config or WorkerConfig()

        self._handlers: dict[str, Callable[..., Any]] = {}
        self._running = False
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._loop_task: asyncio.Task[None] | None = None
        self._stats = {
            "started_at": None,
            "tasks_completed": 0,
            "tasks_failed": 0,
        }

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def handler(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator: register *func* as the handler for tasks with ``handler=name``.

        Example::

            @worker.handler("send_email")
            async def send_email(params: dict) -> dict:
                return {"sent": True}
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._handlers[name] = func
            return func

        return decorator

    def register_handler(self, name: str, func: Callable[..., Any]) -> None:
        """Register *func* as the handler for tasks with ``handler=name``."""
        self._handlers[name] = func

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_count(self) -> int:
        """Number of tasks currently executing."""
        return len(self._active_tasks)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the worker loop."""
        if self._running:
            return
        self._running = True
        self._stats["started_at"] = datetime.now()
        self._loop_task = asyncio.create_task(
            self._worker_loop(), name="task-worker-loop"
        )
        logger.info(
            "TaskWorker started (max_concurrent=%d)", self.config.max_concurrent
        )

    async def stop(self, wait: bool = True) -> None:
        """Stop the worker.

        Args:
            wait: When ``True``, drain active tasks before cancelling the loop.
        """
        self._running = False

        if wait and self._active_tasks:
            logger.info(
                "TaskWorker draining %d active task(s)…", len(self._active_tasks)
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_tasks.values(), return_exceptions=True),
                    timeout=self.config.shutdown_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Shutdown timeout reached — cancelling %d task(s)",
                    len(self._active_tasks),
                )
                for t in self._active_tasks.values():
                    t.cancel()

        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

        logger.info("TaskWorker stopped")

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        """Main loop: poll the queue and dispatch tasks concurrently."""
        while self._running:
            try:
                task = await self.queue.get_next(
                    wait=True, timeout=self.config.poll_interval
                )
                if task is not None:
                    await self._semaphore.acquire()
                    asyncio_task = asyncio.create_task(
                        self._execute_task(task),
                        name=f"task-{task.id}",
                    )
                    self._active_tasks[task.id] = asyncio_task
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Worker loop error: %s", exc)
                await asyncio.sleep(self.config.poll_interval)

    async def _execute_task(self, task: Task) -> None:
        """Execute *task*, updating queue state on completion or failure."""
        try:
            handler = self._handlers.get(task.handler)
            if handler is None:
                raise ValueError(
                    f"No handler registered for {task.handler!r} (task {task.id!r})"
                )

            timeout = task.timeout or self.config.default_timeout

            try:
                if inspect.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(
                        handler(task.params), timeout=timeout
                    )
                else:
                    loop = asyncio.get_running_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, handler, task.params),
                        timeout=timeout,
                    )

                await self.queue.complete(task.id, result=result)
                self._stats["tasks_completed"] += 1
                logger.info("Task completed: %s (%s)", task.id, task.name)

            except asyncio.TimeoutError:
                error_msg = f"Timed out after {timeout}s"
                await self.queue.fail(task.id, error_msg)
                self._stats["tasks_failed"] += 1
                logger.error("Task timed out: %s", task.id)

        except Exception as exc:
            await self.queue.fail(task.id, str(exc))
            self._stats["tasks_failed"] += 1
            logger.error("Task failed: %s — %s", task.id, exc)

        finally:
            self._active_tasks.pop(task.id, None)
            self._semaphore.release()

    # ------------------------------------------------------------------
    # Inline execution (bypasses queue)
    # ------------------------------------------------------------------

    async def execute_now(self, task: Task) -> Any:
        """Execute *task* immediately without going through the queue.

        Raises:
            ValueError: No handler registered for ``task.handler``.
            Exception:  Any exception raised by the handler.
        """
        handler = self._handlers.get(task.handler)
        if handler is None:
            raise ValueError(
                f"No handler registered for {task.handler!r} (task {task.id!r})"
            )

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        timeout = task.timeout or self.config.default_timeout

        try:
            if inspect.iscoroutinefunction(handler):
                result = await asyncio.wait_for(handler(task.params), timeout=timeout)
            else:
                loop = asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, handler, task.params),
                    timeout=timeout,
                )

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            task.result = result
            return result

        except Exception:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now()
            raise

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of worker statistics."""
        started_at = self._stats["started_at"]
        return {
            "running": self._running,
            "started_at": started_at.isoformat() if started_at else None,
            "active_tasks": self.active_count,
            "max_concurrent": self.config.max_concurrent,
            "tasks_completed": self._stats["tasks_completed"],
            "tasks_failed": self._stats["tasks_failed"],
            "registered_handlers": list(self._handlers),
        }


# ---------------------------------------------------------------------------
# Convenience: create_task_handler decorator
# ---------------------------------------------------------------------------


def create_task_handler(func: Callable[..., Any]) -> Callable[[dict[str, Any]], Any]:
    """Decorator that adapts a typed function to accept a ``params`` dict.

    Allows handlers to be written with explicit keyword arguments instead of
    manually unpacking a dict.

    Example::

        @create_task_handler
        async def my_handler(host: str, port: int = 22) -> dict:
            ...

        # The wrapper is called as: await my_handler({"host": "example.com"})
    """
    sig = inspect.signature(func)

    @functools.wraps(func)
    async def wrapper(params: dict[str, Any]) -> Any:
        kwargs: dict[str, Any] = {}
        for param_name, param in sig.parameters.items():
            if param_name in params:
                kwargs[param_name] = params[param_name]
            elif param.default is inspect.Parameter.empty:
                raise ValueError(
                    f"Missing required parameter {param_name!r} for handler "
                    f"{func.__name__!r}"
                )
        if inspect.iscoroutinefunction(func):
            return await func(**kwargs)
        return func(**kwargs)

    return wrapper
