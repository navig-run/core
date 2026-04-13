"""Task queue with priority ordering, dependency management, and optional persistence."""

from __future__ import annotations

import asyncio
import heapq
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class TaskStatus(str, Enum):
    """Task execution status values."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING = "waiting"  # Waiting for dependencies to complete


class TaskPriority(int, Enum):
    """Named priority levels (lower integer = higher priority)."""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 50
    LOW = 100
    BACKGROUND = 200


@dataclass
class Task:
    """A unit of deferred work.

    Attributes:
        name:        Human-readable label.
        handler:     Name of the registered handler function to call.
        params:      Arguments forwarded to the handler.
        priority:    Scheduling priority (lower = higher priority).
        dependencies: IDs of tasks that must complete before this one runs.
        max_retries: Maximum number of automatic retries on failure.
        retry_delay: Delay in seconds between retries.
        timeout:     Per-execution timeout in seconds (``None`` = use worker default).
        id:          Stable identifier (auto-generated 8-char UUID prefix).
        created_at:  Wall-clock creation time.
        started_at:  Time execution began (set by worker/queue).
        completed_at: Time execution finished.
        status:      Current lifecycle status.
        result:      Return value from handler on success.
        error:       Error message on failure.
        retry_count: Number of retry attempts so far.
        meta:        Arbitrary caller-supplied metadata.
    """

    name: str
    handler: str
    params: dict[str, Any] = field(default_factory=dict)
    priority: int = TaskPriority.NORMAL.value
    dependencies: list[str] = field(default_factory=list)
    max_retries: int = 0
    retry_delay: float = 5.0
    timeout: float | None = None

    # Auto-managed fields — do not set manually unless restoring from disk.
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    retry_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other: Task) -> bool:
        """Heap ordering: lower priority value → earlier execution; ties broken by creation time."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "handler": self.handler,
            "params": self.params,
            "priority": self.priority,
            "dependencies": self.dependencies,
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "timeout": self.timeout,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status.value,
            # Coerce result to string for JSON safety; callers should store
            # JSON-serialisable results for faithful round-tripping.
            "result": str(self.result) if self.result is not None else None,
            "error": self.error,
            "retry_count": self.retry_count,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        task = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data["name"],
            handler=data["handler"],
            params=data.get("params", {}),
            priority=data.get("priority", TaskPriority.NORMAL.value),
            dependencies=data.get("dependencies", []),
            max_retries=data.get("max_retries", 0),
            retry_delay=data.get("retry_delay", 5.0),
            timeout=data.get("timeout"),
            meta=data.get("meta", {}),
        )

        for attr, key in (
            ("created_at", "created_at"),
            ("started_at", "started_at"),
            ("completed_at", "completed_at"),
        ):
            raw = data.get(key)
            if raw:
                setattr(task, attr, datetime.fromisoformat(raw))

        if data.get("status"):
            task.status = TaskStatus(data["status"])

        task.result = data.get("result")
        task.error = data.get("error")
        task.retry_count = data.get("retry_count", 0)

        return task


class TaskQueue:
    """Priority-based task queue with dependency management.

    Features:
    - Min-heap priority ordering.
    - DAG dependency tracking (tasks wait until all dependencies complete).
    - Optional persistence to disk (JSON).
    - Status tracking and bulk cleanup of terminal-state tasks.

    Example::

        queue = TaskQueue()

        t1 = await queue.add(Task(
            name="backup-db",
            handler="backup_database",
            params={"host": "prod-db"},
            priority=TaskPriority.HIGH.value,
        ))

        t2 = await queue.add(Task(
            name="notify",
            handler="send_notification",
            dependencies=[t1.id],
        ))

        task = await queue.get_next()
        await queue.complete(task.id, result={"bytes": 1_200_000})
    """

    def __init__(self, persist_path: str | None = None) -> None:
        self._heap: list[Task] = []
        self._tasks: dict[str, Task] = {}
        self._completed: set[str] = set()
        self._lock = asyncio.Lock()
        self._task_added = asyncio.Event()

        self._persist_path: Path | None = None
        if persist_path:
            self._persist_path = Path(persist_path).expanduser()
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of tasks currently in the ready heap."""
        return len(self._heap)

    @property
    def total(self) -> int:
        """Total number of tracked tasks (all statuses)."""
        return len(self._tasks)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    async def add(self, task: Task) -> Task:
        """Enqueue *task*.  Raises ``ValueError`` on duplicate ID."""
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"Task ID already exists: {task.id!r}")

            # Warn if a declared dependency is not yet known.
            for dep_id in task.dependencies:
                if dep_id not in self._tasks and dep_id not in self._completed:
                    logger.warning(
                        "Task %s depends on unknown task %s", task.id, dep_id
                    )

            deps_satisfied = all(
                dep_id in self._completed for dep_id in task.dependencies
            )
            task.status = TaskStatus.QUEUED if deps_satisfied else TaskStatus.WAITING

            self._tasks[task.id] = task

            if task.status == TaskStatus.QUEUED:
                heapq.heappush(self._heap, task)
                self._task_added.set()

            logger.debug("Task added: %s (%s)", task.id, task.name)
            self._persist()
            return task

    async def get_next(
        self, wait: bool = False, timeout: float | None = None
    ) -> Task | None:
        """Return the next ready task, optionally blocking until one is available.

        Args:
            wait:    Block until a task is available or *timeout* expires.
            timeout: Maximum seconds to wait (``None`` = wait indefinitely).

        Returns:
            The task with its status set to ``RUNNING``, or ``None``.
        """
        deadline = (
            asyncio.get_running_loop().time() + timeout
            if timeout is not None
            else None
        )

        while True:
            async with self._lock:
                task = self._pop_ready()
                if task is not None:
                    self._persist()
                    return task
                self._task_added.clear()

            if not wait:
                return None

            remaining = (
                max(0.0, deadline - asyncio.get_running_loop().time())
                if deadline is not None
                else None
            )
            if remaining is not None and remaining <= 0:
                return None

            try:
                if remaining is not None:
                    await asyncio.wait_for(self._task_added.wait(), remaining)
                else:
                    await self._task_added.wait()
            except asyncio.TimeoutError:
                return None

    def _pop_ready(self) -> Task | None:
        """Pop the highest-priority task whose dependencies are met.

        Must be called while holding ``self._lock``.
        """
        skipped: list[Task] = []
        result: Task | None = None

        while self._heap:
            task = heapq.heappop(self._heap)

            if task.status == TaskStatus.CANCELLED:
                continue  # Discard; never put back

            deps_met = all(dep_id in self._completed for dep_id in task.dependencies)
            if deps_met:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()
                result = task
                break
            else:
                task.status = TaskStatus.WAITING
                skipped.append(task)

        for t in skipped:
            heapq.heappush(self._heap, t)

        return result

    async def complete(self, task_id: str, result: Any = None) -> Task:
        """Mark *task_id* as completed and unblock dependent tasks."""
        async with self._lock:
            task = self._require_task(task_id)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            task.result = result
            self._completed.add(task_id)
            self._unblock_waiting_tasks()
            logger.debug("Task completed: %s (%s)", task_id, task.name)
            self._persist()
            return task

    async def fail(
        self,
        task_id: str,
        error: str,
        retry: bool = True,
    ) -> Task:
        """Mark *task_id* as failed, scheduling a retry when applicable."""
        async with self._lock:
            task = self._require_task(task_id)
            task.error = error
            task.retry_count += 1

            if retry and task.retry_count <= task.max_retries:
                task.status = TaskStatus.QUEUED
                asyncio.create_task(
                    self._delayed_requeue(task),
                    name=f"task-retry-{task_id}",
                )
                logger.debug(
                    "Task retry scheduled: %s (attempt %d)", task_id, task.retry_count
                )
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
                logger.debug(
                    "Task failed permanently: %s (%s) — %s", task_id, task.name, error
                )

            self._persist()
            return task

    async def cancel(self, task_id: str) -> Task:
        """Cancel a pending or waiting task."""
        async with self._lock:
            task = self._require_task(task_id)
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                raise ValueError(
                    f"Cannot cancel a {task.status.value!r} task ({task_id!r})"
                )
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()
            logger.debug("Task cancelled: %s", task_id)
            self._persist()
            return task

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, task_id: str) -> Task | None:
        """Return task by ID, or ``None`` if not found."""
        return self._tasks.get(task_id)

    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> list[Task]:
        """Return up to *limit* tasks, optionally filtered by *status*."""
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: (t.priority, t.created_at))
        return tasks[:limit]

    async def clear_completed(self, older_than_hours: int = 24) -> int:
        """Remove terminal-state tasks older than *older_than_hours* hours.

        Returns the number of tasks removed.
        """
        async with self._lock:
            cutoff = datetime.now()
            terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            to_remove = [
                task_id
                for task_id, task in self._tasks.items()
                if task.status in terminal
                and task.completed_at is not None
                and (cutoff - task.completed_at).total_seconds() / 3600
                > older_than_hours
            ]
            for task_id in to_remove:
                del self._tasks[task_id]
                self._completed.discard(task_id)
            self._persist()
            return len(to_remove)

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of queue statistics."""
        status_counts: dict[str, int] = {}
        for task in self._tasks.values():
            status_counts[task.status.value] = (
                status_counts.get(task.status.value, 0) + 1
            )
        return {
            "total_tasks": len(self._tasks),
            "heap_size": len(self._heap),
            "completed_count": len(self._completed),
            "status_counts": status_counts,
        }

    # ------------------------------------------------------------------
    # Internal helpers (must be called under self._lock unless noted)
    # ------------------------------------------------------------------

    def _require_task(self, task_id: str) -> Task:
        """Return the task or raise ``ValueError``."""
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id!r}")
        return task

    def _unblock_waiting_tasks(self) -> None:
        """Push any WAITING tasks whose dependencies are now fully met."""
        for task in self._tasks.values():
            if task.status == TaskStatus.WAITING:
                if all(dep_id in self._completed for dep_id in task.dependencies):
                    task.status = TaskStatus.QUEUED
                    heapq.heappush(self._heap, task)
                    self._task_added.set()

    async def _delayed_requeue(self, task: Task) -> None:
        """Re-add *task* to the heap after its ``retry_delay``."""
        await asyncio.sleep(task.retry_delay)
        async with self._lock:
            if task.status == TaskStatus.QUEUED:
                heapq.heappush(self._heap, task)
                self._task_added.set()
                self._persist()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        """Write current queue state to disk (no-op when no path is configured)."""
        if self._persist_path is None:
            return
        try:
            data = {
                "tasks": {tid: t.to_dict() for tid, t in self._tasks.items()},
                "completed": list(self._completed),
            }
            self._persist_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to persist task queue: %s", exc)

    def _load_from_disk(self) -> None:
        """Restore queue state from disk on startup."""
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            raw = self._persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            logger.error("Failed to load task queue from disk: %s", exc)
            return

        self._completed = set(data.get("completed", []))

        for task_data in data.get("tasks", {}).values():
            task = Task.from_dict(task_data)
            self._tasks[task.id] = task

            # Only re-heap tasks that were actively queued (not waiting/running).
            if task.status == TaskStatus.QUEUED:
                heapq.heappush(self._heap, task)
            elif task.status == TaskStatus.RUNNING:
                # Tasks that were RUNNING when the process died are re-queued so
                # they don't silently vanish; the worker will execute them again.
                task.status = TaskStatus.QUEUED
                heapq.heappush(self._heap, task)

        logger.info("Loaded %d task(s) from disk", len(self._tasks))
