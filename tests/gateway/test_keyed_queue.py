"""Tests for navig.gateway.keyed_queue — per-key async serialization."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from navig.gateway.keyed_queue import KeyedQueue, _enqueue_keyed_task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _append_after(results: list, key: str, value: Any, delay: float = 0.0) -> Any:
    if delay:
        await asyncio.sleep(delay)
    results.append((key, value))
    return value


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_key_is_serialized():
    """Tasks for the same key must execute in enqueue order."""
    results = []
    q = KeyedQueue()

    t1 = q.enqueue("chat1", _append_after(results, "chat1", 1, delay=0.02))
    t2 = q.enqueue("chat1", _append_after(results, "chat1", 2))
    t3 = q.enqueue("chat1", _append_after(results, "chat1", 3))

    await asyncio.gather(t1, t2, t3)
    assert [v for _, v in results] == [1, 2, 3]


@pytest.mark.asyncio
async def test_different_keys_run_concurrently():
    """Tasks for different keys run in parallel, not serially.

    Proven structurally rather than by wall-clock. This used to run two 40 ms sleeps
    and assert `elapsed < 0.07` — a 30 ms margin, which is nothing on a box running 16
    xdist workers (Windows' default timer granularity alone is ~15 ms). It passed solo
    and failed in the full suite: a scheduling-jitter measurement, not a concurrency
    check.

    Each task now announces itself and then waits for the other. Overlap is the only
    way both can finish: if the queue serialized them, the first would block forever on
    a task that cannot start, and `wait_for` fails the test with a real diagnosis. The
    timeout is generous on purpose — it is only ever reached when the behaviour is
    genuinely broken, so load can never make it lie.
    """
    q = KeyedQueue()
    a_started, b_started = asyncio.Event(), asyncio.Event()
    results: list[tuple[str, str]] = []

    async def _a() -> str:
        a_started.set()
        await asyncio.wait_for(b_started.wait(), timeout=10)
        results.append(("a", "a"))
        return "a"

    async def _b() -> str:
        b_started.set()
        await asyncio.wait_for(a_started.wait(), timeout=10)
        results.append(("b", "b"))
        return "b"

    await asyncio.gather(q.enqueue("a", _a()), q.enqueue("b", _b()))
    assert {v for _, v in results} == {"a", "b"}


@pytest.mark.asyncio
async def test_tail_cleaned_up_after_completion():
    q = KeyedQueue()
    t = q.enqueue("k", asyncio.sleep(0))
    await t
    # Allow cleanup callback to run
    await asyncio.sleep(0)
    assert "k" not in q.active_keys


@pytest.mark.asyncio
async def test_active_keys_reflects_pending():
    q = KeyedQueue()
    slow = q.enqueue("slow_key", asyncio.sleep(0.1))
    assert "slow_key" in q.active_keys
    await slow
    await asyncio.sleep(0)
    assert "slow_key" not in q.active_keys


@pytest.mark.asyncio
async def test_returns_coroutine_result():
    q = KeyedQueue()
    result = await q.enqueue("x", _append_after([], "x", 42))
    assert result == 42


@pytest.mark.asyncio
async def test_exception_in_predecessor_does_not_block_successor():
    """If one task raises, the next task for the same key still runs."""
    results = []

    async def _fail():
        raise RuntimeError("boom")

    q = KeyedQueue()
    _t1 = q.enqueue("k", _fail())
    t2 = q.enqueue("k", _append_after(results, "k", "ok"))
    await asyncio.gather(_t1, t2, return_exceptions=True)

    assert any(v == "ok" for _, v in results)


@pytest.mark.asyncio
async def test_primitive_enqueue_keyed_task():
    """Low-level _enqueue_keyed_task produces same behaviour."""
    tails: dict = {}
    results = []
    t1 = _enqueue_keyed_task(tails, "k", _append_after(results, "k", 1, delay=0.02))
    t2 = _enqueue_keyed_task(tails, "k", _append_after(results, "k", 2))
    await asyncio.gather(t1, t2)
    assert [v for _, v in results] == [1, 2]


@pytest.mark.asyncio
async def test_many_tasks_same_key_ordered():
    q = KeyedQueue()
    results = []
    tasks = [q.enqueue("chat", _append_after(results, "chat", i)) for i in range(10)]
    await asyncio.gather(*tasks)
    assert [v for _, v in results] == list(range(10))
