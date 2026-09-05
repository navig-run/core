"""Tests for the GC-safe background-task helper (navig.core.background.spawn).

The whole point of ``spawn`` is that a fire-and-forget task survives even when the
caller keeps no reference to it — the plain ``asyncio.ensure_future`` result is only
weakly held by the loop and can be collected before it runs. These tests exercise
exactly that (drop the handle, force a collection, assert it still ran) plus the
registry-release and exception-logging behaviour.
"""

from __future__ import annotations

import asyncio
import gc

from navig.core import background
from navig.core.background import spawn


async def test_spawn_runs_the_coroutine():
    ran = asyncio.Event()

    async def work():
        ran.set()

    spawn(work())
    await asyncio.wait_for(ran.wait(), timeout=1.0)
    assert ran.is_set()


async def test_spawn_survives_dropped_reference_and_gc():
    """The caller keeps NO handle and we force a GC — the task must still run.

    A bare ``asyncio.ensure_future(work())`` here would be eligible for collection
    the moment the statement returns; ``spawn`` holds a strong ref so it can't be.
    """
    marker: list[str] = []

    async def work():
        # yield control so the task is genuinely pending across the gc.collect()
        await asyncio.sleep(0.02)
        marker.append("done")

    # No local variable holds the task — spawn's internal set is the only ref.
    spawn(work())
    gc.collect()
    # Let the loop drain the pending task.
    await asyncio.sleep(0.1)
    assert marker == ["done"]


async def test_spawn_releases_reference_when_done():
    async def work():
        return None

    task = spawn(work())
    assert task in background._bg_tasks
    await task
    # add_done_callback fires on the loop; give it a tick to run.
    await asyncio.sleep(0)
    assert task not in background._bg_tasks


async def test_spawn_logs_exception_and_does_not_raise(navig_log_capture):
    # navig_log_capture (not caplog): background.spawn logs via navig.tasks, and navig's loggers
    # set propagate=False, so caplog can't see it (order-dependently) — tests/conftest.py.
    async def boom():
        raise RuntimeError("kaboom")

    task = spawn(boom(), name="boomer")
    # awaiting a failed task re-raises — retrieve via gather with return_exceptions
    results = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(results[0], RuntimeError)
    await asyncio.sleep(0)  # let the done-callback log

    assert any("boomer" in m and "kaboom" in m for m in navig_log_capture), (
        f"expected a warning naming the task and error, got: {list(navig_log_capture)}"
    )
    assert task not in background._bg_tasks


async def test_spawn_sets_task_name():
    async def work():
        return None

    task = spawn(work(), name="my-task")
    assert task.get_name() == "my-task"
    await task
