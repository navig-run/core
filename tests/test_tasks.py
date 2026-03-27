"""Unit tests for the task queue module."""

import pytest

from navig.tasks import (
    Task,
    TaskPriority,
    TaskQueue,
    TaskStatus,
    TaskWorker,
    WorkerConfig,
)


class TestTask:
    """Tests for Task dataclass."""

    def test_task_creation(self):
        """Task should be created with defaults."""
        task = Task(
            name="test-task",
            handler="test_handler",
        )

        assert task.id is not None
        assert task.name == "test-task"
        assert task.handler == "test_handler"
        assert task.status == TaskStatus.PENDING
        assert task.priority == TaskPriority.NORMAL.value

    def test_task_priority_ordering(self):
        """Tasks should compare by priority."""
        high = Task(name="high", handler="h", priority=TaskPriority.HIGH.value)
        normal = Task(name="normal", handler="h", priority=TaskPriority.NORMAL.value)
        low = Task(name="low", handler="h", priority=TaskPriority.LOW.value)

        assert high < normal < low

    def test_task_to_dict(self):
        """Task should serialize to dict."""
        task = Task(
            name="serialize-test",
            handler="test_handler",
            params={"key": "value"},
            priority=TaskPriority.HIGH.value,
        )

        data = task.to_dict()

        assert data["name"] == "serialize-test"
        assert data["handler"] == "test_handler"
        assert data["params"] == {"key": "value"}
        assert data["priority"] == TaskPriority.HIGH.value

    def test_task_from_dict(self):
        """Task should deserialize from dict."""
        data = {
            "id": "test123",
            "name": "from-dict",
            "handler": "test_handler",
            "params": {"x": 1},
            "priority": 10,
            "status": "queued",
        }

        task = Task.from_dict(data)

        assert task.id == "test123"
        assert task.name == "from-dict"
        assert task.params == {"x": 1}
        assert task.status == TaskStatus.QUEUED


class TestTaskQueue:
    """Tests for TaskQueue class."""

    @pytest.fixture
    def queue(self):
        """Create empty task queue."""
        return TaskQueue()

    @pytest.mark.asyncio
    async def test_add_task(self, queue):
        """Should add task to queue."""
        task = Task(name="test", handler="h")

        added = await queue.add(task)

        assert added.id == task.id
        assert added.status == TaskStatus.QUEUED
        assert queue.size == 1

    @pytest.mark.asyncio
    async def test_get_next_task(self, queue):
        """Should get highest priority task."""
        low = Task(name="low", handler="h", priority=100)
        high = Task(name="high", handler="h", priority=10)

        await queue.add(low)
        await queue.add(high)

        next_task = await queue.get_next()

        assert next_task.name == "high"
        assert next_task.status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_complete_task(self, queue):
        """Should mark task as completed."""
        task = Task(name="test", handler="h")
        await queue.add(task)

        next_task = await queue.get_next()
        completed = await queue.complete(next_task.id, result={"done": True})

        assert completed.status == TaskStatus.COMPLETED
        assert completed.result == {"done": True}

    @pytest.mark.asyncio
    async def test_fail_task(self, queue):
        """Should mark task as failed."""
        task = Task(name="test", handler="h", max_retries=0)
        await queue.add(task)

        next_task = await queue.get_next()
        failed = await queue.fail(next_task.id, "Test error", retry=False)

        assert failed.status == TaskStatus.FAILED
        assert failed.error == "Test error"

    @pytest.mark.asyncio
    async def test_cancel_task(self, queue):
        """Should cancel pending task."""
        task = Task(name="test", handler="h")
        await queue.add(task)

        cancelled = await queue.cancel(task.id)

        assert cancelled.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_task_dependencies(self, queue):
        """Tasks with dependencies should wait."""
        first = Task(name="first", handler="h")
        await queue.add(first)

        dependent = Task(name="dependent", handler="h", dependencies=[first.id])
        await queue.add(dependent)

        # Dependent should be waiting
        assert dependent.status == TaskStatus.WAITING

        # Complete first task
        next_task = await queue.get_next()
        await queue.complete(next_task.id)

        # Now dependent should be queued
        dep_task = await queue.get(dependent.id)
        assert dep_task.status == TaskStatus.QUEUED

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        """Queue should persist and restore."""
        persist_path = tmp_path / "queue.json"

        # Create and add task
        queue1 = TaskQueue(persist_path=str(persist_path))
        task = Task(name="persist-test", handler="h")
        await queue1.add(task)

        # Create new queue from same file
        queue2 = TaskQueue(persist_path=str(persist_path))

        assert queue2.total == 1
        loaded = await queue2.get(task.id)
        assert loaded.name == "persist-test"

    def test_stats(self, queue):
        """Should return queue statistics."""
        stats = queue.get_stats()

        assert "total_tasks" in stats
        assert "heap_size" in stats
        assert "completed_count" in stats


class TestTaskWorker:
    """Tests for TaskWorker class."""

    @pytest.fixture
    def queue(self):
        return TaskQueue()

    @pytest.fixture
    def worker(self, queue):
        return TaskWorker(queue, WorkerConfig(max_concurrent=2))

    def test_register_handler(self, worker):
        """Should register handlers."""

        @worker.handler("test_handler")
        async def test_handler(params):
            return {"result": params.get("x", 0) * 2}

        assert "test_handler" in worker._handlers

    @pytest.mark.asyncio
    async def test_execute_now(self, worker):
        """Should execute task immediately."""

        @worker.handler("double")
        async def double_handler(params):
            return params["x"] * 2

        task = Task(name="double", handler="double", params={"x": 5})
        result = await worker.execute_now(task)

        assert result == 10
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_now_failure(self, worker):
        """Should handle execution failure."""

        @worker.handler("fail")
        async def fail_handler(params):
            raise ValueError("Intentional failure")

        task = Task(name="fail", handler="fail")

        with pytest.raises(ValueError):
            await worker.execute_now(task)

        assert task.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_worker_stats(self, worker):
        """Should return worker statistics."""
        stats = worker.get_stats()

        assert "running" in stats
        assert "active_tasks" in stats
        assert "max_concurrent" in stats
        assert "registered_handlers" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
