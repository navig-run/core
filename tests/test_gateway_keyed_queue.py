"""Tests for navig.gateway.keyed_queue — per-key task serialization."""
from __future__ import annotations

import asyncio

import pytest

from navig.gateway.keyed_queue import KeyedQueue


class TestKeyedQueue:
    async def test_single_task_runs(self):
        q = KeyedQueue()
        result = []

        async def work():
            result.append(1)

        task = q.enqueue("chat1", work())
        await task
        assert result == [1]

    async def test_tasks_for_same_key_serialized(self):
        q = KeyedQueue()
        order = []

        async def slow():
            await asyncio.sleep(0.02)
            order.append("slow")

        async def fast():
            order.append("fast")

        t1 = q.enqueue("key", slow())
        t2 = q.enqueue("key", fast())
        await asyncio.gather(t1, t2)
        # slow finishes first because it was enqueued first
        assert order == ["slow", "fast"]

    async def test_tasks_for_different_keys_concurrent(self):
        q = KeyedQueue()
        started = []

        async def worker(name: str):
            started.append(name)
            await asyncio.sleep(0.01)

        t1 = q.enqueue("a", worker("a"))
        t2 = q.enqueue("b", worker("b"))
        await asyncio.gather(t1, t2)
        assert set(started) == {"a", "b"}

    async def test_active_keys_while_running(self):
        q = KeyedQueue()
        ev = asyncio.Event()

        async def waiter():
            await ev.wait()

        q.enqueue("chat1", waiter())
        assert "chat1" in q.active_keys
        ev.set()
        await asyncio.sleep(0.01)  # let cleanup callback run

    async def test_key_removed_after_completion(self):
        q = KeyedQueue()

        async def noop():
            pass

        task = q.enqueue("chat2", noop())
        await task
        await asyncio.sleep(0)  # allow done callback
        assert "chat2" not in q.active_keys

    async def test_exception_in_task_does_not_block_next(self):
        q = KeyedQueue()
        results = []

        async def fail():
            raise ValueError("boom")

        async def success():
            results.append("ok")

        t1 = q.enqueue("k", fail())
        t2 = q.enqueue("k", success())

        try:
            await t1
        except ValueError:
            pass
        await t2
        assert "ok" in results

    async def test_returns_asyncio_task(self):
        q = KeyedQueue()

        async def noop():
            return 42

        task = q.enqueue("x", noop())
        assert isinstance(task, asyncio.Task)
        result = await task
        assert result == 42
