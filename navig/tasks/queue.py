"""Task queue with priority, dependencies, and persistence."""

import asyncio
import heapq
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING = "waiting"  # Waiting for dependencies


class TaskPriority(int, Enum):
    """Task priority levels (lower = higher priority)."""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 50
    LOW = 100
    BACKGROUND = 200


@dataclass
class Task:
    """
    Task definition with priority, dependencies, and retry configuration.

    Attributes:
        id: Unique task identifier
        name: Human-readable task name
        handler: Name of handler function to execute
        params: Parameters to pass to handler
        priority: Task priority (lower = higher priority)
        dependencies: List of task IDs that must complete first
        max_retries: Maximum retry attempts on failure
        retry_delay: Delay in seconds between retries
        timeout: Task execution timeout in seconds
        created_at: Task creation timestamp
        started_at: Execution start timestamp
        completed_at: Execution completion timestamp
        status: Current task status
        result: Task result on completion
        error: Error message on failure
        retry_count: Current retry attempt number
        meta: Additional metadata
    """

    name: str
    handler: str
    params: Dict[str, Any] = field(default_factory=dict)
    priority: int = TaskPriority.NORMAL.value
    dependencies: List[str] = field(default_factory=list)
    max_retries: int = 0
    retry_delay: float = 5.0
    timeout: Optional[float] = None

    # Auto-generated/managed fields
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    retry_count: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other: "Task") -> bool:
        """Compare by priority for heap ordering."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
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
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "status": self.status.value,
            "result": str(self.result) if self.result else None,
            "error": self.error,
            "retry_count": self.retry_count,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Deserialize from dictionary."""
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

        if data.get("created_at"):
            task.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("started_at"):
            task.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            task.completed_at = datetime.fromisoformat(data["completed_at"])
        if data.get("status"):
            task.status = TaskStatus(data["status"])

        task.result = data.get("result")
        task.error = data.get("error")
        task.retry_count = data.get("retry_count", 0)

        return task


class TaskQueue:
    """
    Priority-based task queue with dependency management.

    Features:
    - Priority-based ordering (heap)
    - Task dependencies (DAG)
    - Persistence to disk
    - Task status tracking
    - Retry support

    Example:
        queue = TaskQueue()

        # Add tasks
        task1 = await queue.add(Task(
            name="backup-db",
            handler="backup_database",
            params={"host": "prod-db"},
            priority=TaskPriority.HIGH.value,
        ))

        task2 = await queue.add(Task(
            name="notify",
            handler="send_notification",
            dependencies=[task1.id],  # Wait for backup
        ))

        # Get next ready task
        task = await queue.get_next()

        # Mark complete
        await queue.complete(task.id, result={"size": "1.2GB"})
    """

    def __init__(self, persist_path: Optional[str] = None):
        """
        Initialize task queue.

        Args:
            persist_path: Optional path to persist queue state
        """
        self._heap: List[Task] = []  # Priority heap
        self._tasks: Dict[str, Task] = {}  # id -> Task
        self._completed: Set[str] = set()  # Completed task IDs
        self._lock = asyncio.Lock()
        self._task_added_event = asyncio.Event()

        self._persist_path = Path(persist_path).expanduser() if persist_path else None
        if self._persist_path:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    @property
    def size(self) -> int:
        """Get number of pending tasks."""
        return len(self._heap)

    @property
    def total(self) -> int:
        """Get total number of tracked tasks."""
        return len(self._tasks)

    async def add(self, task: Task) -> Task:
        """
        Add task to queue.

        Args:
            task: Task to add

        Returns:
            Added task with ID
        """
        async with self._lock:
            # Check for duplicate ID
            if task.id in self._tasks:
                raise ValueError(f"Task with ID {task.id} already exists")

            # Check dependency validity
            for dep_id in task.dependencies:
                if dep_id not in self._tasks and dep_id not in self._completed:
                    # Allow non-existent dependencies (they might be added later)
                    logger.warning(f"Task {task.id} depends on unknown task {dep_id}")

            # Determine initial status
            if task.dependencies:
                # Check if dependencies are met
                deps_met = all(
                    dep_id in self._completed for dep_id in task.dependencies
                )
                task.status = TaskStatus.QUEUED if deps_met else TaskStatus.WAITING
            else:
                task.status = TaskStatus.QUEUED

            # Add to tracking
            self._tasks[task.id] = task

            # Add to heap if ready
            if task.status == TaskStatus.QUEUED:
                heapq.heappush(self._heap, task)
                self._task_added_event.set()

            logger.debug(f"Task added: {task.id} ({task.name})")
            self._persist()

            return task

    async def get_next(
        self, wait: bool = False, timeout: Optional[float] = None
    ) -> Optional[Task]:
        """
        Get next ready task from queue.

        If wait=True, waits until a task is available or timeout occurs.
        """
        start_time = datetime.now()
        while True:
            async with self._lock:
                while self._heap:
                    task = heapq.heappop(self._heap)

                    # Skip cancelled tasks
                    if task.status == TaskStatus.CANCELLED:
                        continue

                    # Check dependencies again
                    deps_met = all(
                        dep_id in self._completed for dep_id in task.dependencies
                    )

                    if deps_met:
                        task.status = TaskStatus.RUNNING
                        task.started_at = datetime.now()
                        self._persist()
                        return task
                    else:
                        # Put back with waiting status
                        task.status = TaskStatus.WAITING
                        heapq.heappush(self._heap, task)
                        continue
                self._task_added_event.clear()

            if not wait:
                return None

            elapsed = (datetime.now() - start_time).total_seconds()
            if timeout and elapsed >= timeout:
                return None

            try:
                if timeout:
                    await asyncio.wait_for(
                        self._task_added_event.wait(), timeout - elapsed
                    )
                else:
                    await self._task_added_event.wait()
            except asyncio.TimeoutError:
                return None

    async def complete(
        self,
        task_id: str,
        result: Any = None,
    ) -> Task:
        """
        Mark task as completed.

        Args:
            task_id: Task ID
            result: Task result

        Returns:
            Completed task
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            task.result = result

            self._completed.add(task_id)

            # Check if any waiting tasks can now run
            self._check_waiting_tasks()

            logger.debug(f"Task completed: {task_id} ({task.name})")
            self._persist()

            return task

    async def fail(
        self,
        task_id: str,
        error: str,
        retry: bool = True,
    ) -> Task:
        """
        Mark task as failed.

        Args:
            task_id: Task ID
            error: Error message
            retry: Whether to retry if possible

        Returns:
            Failed task
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")

            task.error = error
            task.retry_count += 1

            # Check retry
            if retry and task.retry_count <= task.max_retries:
                task.status = TaskStatus.QUEUED
                # Re-add to heap after delay
                asyncio.create_task(self._delayed_requeue(task))
                logger.debug(
                    f"Task retry scheduled: {task_id} (attempt {task.retry_count})"
                )
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
                logger.debug(f"Task failed: {task_id} ({task.name}) - {error}")

            self._persist()
            return task

    async def _delayed_requeue(self, task: Task):
        """Re-add task to queue after delay."""
        await asyncio.sleep(task.retry_delay)
        async with self._lock:
            if task.status == TaskStatus.QUEUED:
                heapq.heappush(self._heap, task)
                self._persist()

    async def cancel(self, task_id: str) -> Task:
        """
        Cancel a pending task.

        Args:
            task_id: Task ID

        Returns:
            Cancelled task
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")

            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                raise ValueError(f"Cannot cancel {task.status.value} task")

            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()

            logger.debug(f"Task cancelled: {task_id}")
            self._persist()

            return task

    async def get(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self._tasks.get(task_id)

    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> List[Task]:
        """
        List tasks with optional status filter.

        Args:
            status: Filter by status
            limit: Maximum tasks to return

        Returns:
            List of tasks
        """
        tasks = list(self._tasks.values())

        if status:
            tasks = [t for t in tasks if t.status == status]

        # Sort by priority then created_at
        tasks.sort(key=lambda t: (t.priority, t.created_at))

        return tasks[:limit]

    async def clear_completed(self, older_than_hours: int = 24) -> int:
        """
        Clear old completed tasks.

        Args:
            older_than_hours: Only clear tasks older than this

        Returns:
            Number of tasks cleared
        """
        async with self._lock:
            cutoff = datetime.now()
            count = 0

            to_remove = []
            for task_id, task in self._tasks.items():
                if task.status in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ):
                    if task.completed_at:
                        age_hours = (cutoff - task.completed_at).total_seconds() / 3600
                        if age_hours > older_than_hours:
                            to_remove.append(task_id)

            for task_id in to_remove:
                del self._tasks[task_id]
                self._completed.discard(task_id)
                count += 1

            self._persist()
            return count

    def _check_waiting_tasks(self):
        """Check if any waiting tasks can now be queued."""
        for task in self._tasks.values():
            if task.status == TaskStatus.WAITING:
                deps_met = all(
                    dep_id in self._completed for dep_id in task.dependencies
                )
                if deps_met:
                    task.status = TaskStatus.QUEUED
                    heapq.heappush(self._heap, task)
                    self._task_added_event.set()

    def _persist(self):
        """Persist queue state to disk."""
        if not self._persist_path:
            return

        try:
            data = {
                "tasks": {
                    task_id: task.to_dict() for task_id, task in self._tasks.items()
                },
                "completed": list(self._completed),
            }

            with open(self._persist_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist task queue: {e}")

    def _load_from_disk(self):
        """Load queue state from disk."""
        if not self._persist_path or not self._persist_path.exists():
            return

        try:
            with open(self._persist_path) as f:
                data = json.load(f)

            self._completed = set(data.get("completed", []))

            for task_id, task_data in data.get("tasks", {}).items():
                task = Task.from_dict(task_data)
                self._tasks[task_id] = task

                # Re-queue pending tasks
                if task.status in (
                    TaskStatus.PENDING,
                    TaskStatus.QUEUED,
                    TaskStatus.WAITING,
                ):
                    if task.status != TaskStatus.WAITING:
                        heapq.heappush(self._heap, task)

            logger.info(f"Loaded {len(self._tasks)} tasks from disk")
        except Exception as e:
            logger.error(f"Failed to load task queue: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        status_counts = {}
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
