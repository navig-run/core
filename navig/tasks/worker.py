"""Task worker for executing queued tasks."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from navig.debug_logger import get_debug_logger

from .queue import Task, TaskQueue, TaskStatus

logger = get_debug_logger()


@dataclass
class WorkerConfig:
    """Task worker configuration."""

    max_concurrent: int = 5
    poll_interval: float = 1.0
    shutdown_timeout: float = 30.0
    default_timeout: float = 300.0  # 5 minutes


class TaskWorker:
    """
    Task worker that processes tasks from a queue.

    Supports:
    - Concurrent task execution
    - Task timeouts
    - Handler registration
    - Graceful shutdown

    Example:
        queue = TaskQueue()
        worker = TaskWorker(queue)

        # Register handlers
        @worker.handler("backup_database")
        async def backup_handler(params):
            host = params["host"]
            # ... backup logic ...
            return {"size": "1.2GB"}

        # Start worker
        await worker.start()

        # Add tasks
        await queue.add(Task(
            name="backup",
            handler="backup_database",
            params={"host": "prod-db"},
        ))

        # Stop worker
        await worker.stop()
    """

    def __init__(
        self,
        queue: TaskQueue,
        config: WorkerConfig | None = None,
    ):
        """
        Initialize task worker.

        Args:
            queue: Task queue to process
            config: Worker configuration
        """
        self.queue = queue
        self.config = config or WorkerConfig()

        self._handlers: dict[str, Callable] = {}
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}  # task_id -> asyncio.Task
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._worker_task: asyncio.Task | None = None
        self._stats = {
            "started_at": None,
            "tasks_completed": 0,
            "tasks_failed": 0,
        }

    def handler(self, name: str):
        """
        Decorator to register a task handler.

        Args:
            name: Handler name (matches Task.handler)

        Example:
            @worker.handler("send_email")
            async def send_email_handler(params):
                # ... send email ...
                return {"sent": True}
        """

        def decorator(func: Callable):
            self._handlers[name] = func
            return func

        return decorator

    def register_handler(self, name: str, func: Callable):
        """
        Register a task handler.

        Args:
            name: Handler name
            func: Handler function (async or sync)
        """
        self._handlers[name] = func

    @property
    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._running

    @property
    def active_tasks(self) -> int:
        """Get number of currently running tasks."""
        return len(self._tasks)

    async def start(self):
        """Start the worker."""
        if self._running:
            return

        self._running = True
        self._stats["started_at"] = datetime.now()
        self._worker_task = asyncio.create_task(self._worker_loop())

        logger.info("TaskWorker started (max_concurrent=%d)", self.config.max_concurrent)

    async def stop(self, wait: bool = True):
        """
        Stop the worker.

        Args:
            wait: Wait for running tasks to complete
        """
        self._running = False

        if wait and self._tasks:
            logger.info("Waiting for %d tasks to complete...", len(self._tasks))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks.values(), return_exceptions=True),
                    timeout=self.config.shutdown_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Shutdown timeout, cancelling remaining tasks")
                for task in self._tasks.values():
                    task.cancel()

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        logger.info("TaskWorker stopped")

    async def _worker_loop(self):
        """Main worker loop."""
        while self._running:
            try:
                # Wait for available slot
                async with self._semaphore:
                    # Get next task
                    task = await self.queue.get_next(
                        wait=True, timeout=self.config.poll_interval
                    )

                    if task:
                        # Start execution
                        asyncio_task = asyncio.create_task(self._execute_task(task))
                        self._tasks[task.id] = asyncio_task

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Worker loop error: %s", e)
                await asyncio.sleep(self.config.poll_interval)

    async def _execute_task(self, task: Task):
        """Execute a single task."""
        try:
            handler = self._handlers.get(task.handler)

            if not handler:
                raise ValueError(f"No handler registered for: {task.handler}")

            # Execute with timeout
            timeout = task.timeout or self.config.default_timeout

            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(
                        handler(task.params),
                        timeout=timeout,
                    )
                else:
                    # Run sync handler in thread pool
                    loop = asyncio.get_running_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, handler, task.params),
                        timeout=timeout,
                    )

                # Mark complete
                await self.queue.complete(task.id, result=result)
                self._stats["tasks_completed"] += 1

                logger.info(f"Task completed: {task.id} ({task.name})")

            except asyncio.TimeoutError:
                await self.queue.fail(task.id, f"Task timed out after {timeout}s")
                self._stats["tasks_failed"] += 1
                logger.error(f"Task timeout: {task.id}")

        except Exception as e:
            await self.queue.fail(task.id, str(e))
            self._stats["tasks_failed"] += 1
            logger.error(f"Task failed: {task.id} - {e}")

        finally:
            self._tasks.pop(task.id, None)

    async def execute_now(self, task: Task) -> Any:
        """
        Execute a task immediately without queuing.

        Args:
            task: Task to execute

        Returns:
            Task result

        Raises:
            ValueError: If no handler is registered
            Exception: If task fails
        """
        handler = self._handlers.get(task.handler)

        if not handler:
            raise ValueError(f"No handler registered for: {task.handler}")

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()

        try:
            timeout = task.timeout or self.config.default_timeout

            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(
                    handler(task.params),
                    timeout=timeout,
                )
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

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now()
            task.error = str(e)
            raise

    def get_stats(self) -> dict[str, Any]:
        """Get worker statistics."""
        return {
            "running": self._running,
            "started_at": (
                self._stats["started_at"].isoformat()
                if self._stats["started_at"]
                else None
            ),
            "active_tasks": self.active_tasks,
            "max_concurrent": self.config.max_concurrent,
            "tasks_completed": self._stats["tasks_completed"],
            "tasks_failed": self._stats["tasks_failed"],
            "registered_handlers": list(self._handlers.keys()),
        }


# Convenience functions for common patterns
def create_task_handler(func: Callable) -> Callable:
    """
    Decorator to create a task-compatible handler.

    Wraps a function to accept a params dict.

    Example:
        @create_task_handler
        async def my_handler(host: str, port: int = 22):
            # ...

        # Can be called as:
        await my_handler({"host": "example.com", "port": 22})
    """
    import functools
    import inspect

    @functools.wraps(func)
    async def wrapper(params: dict):
        sig = inspect.signature(func)

        # Map params to function arguments
        kwargs = {}
        for name, param in sig.parameters.items():
            if name in params:
                kwargs[name] = params[name]
            elif param.default is inspect.Parameter.empty:
                raise ValueError(f"Missing required parameter: {name}")

        if asyncio.iscoroutinefunction(func):
            return await func(**kwargs)
        else:
            return func(**kwargs)

    return wrapper
