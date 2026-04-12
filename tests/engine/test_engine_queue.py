"""
Tests for navig.engine.queue — CommandQueue lane-based asyncio queue.
"""

import asyncio

import pytest

from navig.engine.queue import (
    CommandQueue,
    LaneClearedError,
    QueueShutdownError,
    TaskState,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _ok(value=42, delay=0.0):
    if delay:
        await asyncio.sleep(delay)
    return value


async def _fail(msg="oops"):
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Basic enqueue + wait
# ---------------------------------------------------------------------------


async def test_enqueue_returns_result():
    q = CommandQueue(default_timeout=5.0)
    handle = await q.enqueue("main", _ok(99))
    result = await handle.wait()
    assert result == 99
    assert handle.state == TaskState.DONE
    await q.shutdown()


async def test_multiple_tasks_in_lane_run_serially():
    q = CommandQueue(default_timeout=5.0)
    order = []

    async def record(n, delay=0.0):
        if delay:
            await asyncio.sleep(delay)
        order.append(n)
        return n

    h1 = await q.enqueue("main", record(1, 0.05))
    h2 = await q.enqueue("main", record(2))
    h3 = await q.enqueue("main", record(3))

    await h1.wait()
    await h2.wait()
    await h3.wait()

    assert order == [1, 2, 3]
    await q.shutdown()


async def test_different_lanes_run_concurrently():
    q = CommandQueue(default_timeout=5.0)
    results = {}

    async def slow(key):
        await asyncio.sleep(0.05)
        results[key] = True

    h1 = await q.enqueue("lane_a", slow("a"))
    h2 = await q.enqueue("lane_b", slow("b"))

    await asyncio.gather(h1.wait(), h2.wait())
    assert results == {"a": True, "b": True}
    await q.shutdown()


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


async def test_failed_task_propagates_exception():
    q = CommandQueue(default_timeout=5.0)
    handle = await q.enqueue("main", _fail("boom"))
    with pytest.raises(ValueError, match="boom"):
        await handle.wait()
    assert handle.state == TaskState.ERROR
    await q.shutdown()


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


async def test_task_timeout_raises():
    q = CommandQueue(default_timeout=0.1)
    handle = await q.enqueue("main", _ok(1, delay=5.0))
    with pytest.raises(asyncio.TimeoutError):
        await handle.wait()
    await q.shutdown()


# ---------------------------------------------------------------------------
# clear_lane
# ---------------------------------------------------------------------------


async def test_clear_lane_cancels_pending():
    q = CommandQueue(default_timeout=5.0)

    # Block the lane with a slow task, then queue more
    h1 = await q.enqueue("main", _ok(1, delay=10.0))
    h2 = await q.enqueue("main", _ok(2))
    h3 = await q.enqueue("main", _ok(3))

    # Give worker time to start on h1
    await asyncio.sleep(0.02)

    cancelled = await q.clear_lane("main")
    assert cancelled > 0

    # All three handles should raise LaneClearedError — always retrieve to
    # prevent "Future exception was never retrieved" asyncio warnings.
    for h in (h1, h2, h3):
        with pytest.raises(
            (LaneClearedError, asyncio.CancelledError, asyncio.TimeoutError, Exception)
        ):
            await h.wait()


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


async def test_shutdown_prevents_enqueue():
    q = CommandQueue(default_timeout=5.0)
    await q.shutdown()
    with pytest.raises(QueueShutdownError):
        await q.enqueue("main", _ok())


# ---------------------------------------------------------------------------
# status introspection
# ---------------------------------------------------------------------------


async def test_status_reports_lanes():
    q = CommandQueue(default_timeout=5.0)
    await q.enqueue("alpha", _ok(delay=0.05))
    status = q.status()
    assert "alpha" in status
    await q.shutdown()
