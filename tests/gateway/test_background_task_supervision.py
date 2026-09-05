"""The gateway's background-task helper must not swallow a dying task.

``_spawn_background_task`` used to attach a done-callback that ONLY discarded the
task from its tracking set — so a coroutine that raised (a monitor loop, a producer,
the proactive engine) vanished with no navig log: asyncio's own "Task exception was
never retrieved" only fires at GC time, if ever. And a monitor's handle in
``_monitor_tasks`` was never cleared on death, so a crashed monitor kept reporting as
running (``_start_monitor`` early-returns while the name is present, so re-enabling it
silently did nothing). Same silent-failure class as the recent monitors/signals work.

These tests borrow the real (unbound) methods onto a minimal stub — instantiating a
full ``NavigGateway`` would wire the whole daemon — and patch the module logger so the
assertion does not depend on navig's ``propagate=False`` logger tree.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from navig.gateway.server import NavigGateway


class _Gw:
    """Minimal stand-in exposing just the background-task helpers under test."""

    _spawn_background_task = NavigGateway._spawn_background_task
    _on_background_task_done = NavigGateway._on_background_task_done
    _spawn_monitor_task = NavigGateway._spawn_monitor_task
    _on_monitor_task_done = NavigGateway._on_monitor_task_done
    is_monitor_running = NavigGateway.is_monitor_running

    def __init__(self) -> None:
        self._background_tasks: set = set()
        self._monitor_tasks: dict = {}


async def _drain() -> None:
    """Let ``add_done_callback`` (scheduled via call_soon) callbacks run."""
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.fixture
def patched_logger(monkeypatch):
    mock_logger = MagicMock()
    monkeypatch.setattr("navig.gateway.server.logger", mock_logger)
    return mock_logger


async def test_failing_background_task_is_logged(patched_logger):
    gw = _Gw()

    async def boom():
        raise RuntimeError("kaboom")

    task = gw._spawn_background_task(boom(), name="unit-task")
    await asyncio.gather(task, return_exceptions=True)
    await _drain()

    assert patched_logger.warning.called, "a dying background task must be logged, not swallowed"
    # the exception the tool logged is the real one
    _, kwargs = patched_logger.warning.call_args
    assert isinstance(kwargs.get("exc_info"), RuntimeError)
    # and it is discarded from tracking (set stays bounded)
    assert task not in gw._background_tasks


async def test_cancelled_background_task_is_not_logged_as_failed(patched_logger):
    gw = _Gw()

    async def sleeper():
        await asyncio.sleep(10)

    task = gw._spawn_background_task(sleeper(), name="cancel-me")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await _drain()

    assert not patched_logger.warning.called, "cancellation is normal shutdown, not a failure"
    assert not patched_logger.error.called
    assert task not in gw._background_tasks


async def test_crashed_monitor_clears_its_tracked_handle(patched_logger):
    gw = _Gw()

    async def boom():
        raise RuntimeError("monitor died")

    task = gw._spawn_monitor_task("webcam", boom())
    assert gw._monitor_tasks.get("webcam") is task  # tracked while alive
    await asyncio.gather(task, return_exceptions=True)
    await _drain()

    # honest state: a crashed monitor is NOT left looking like it is running
    assert "webcam" not in gw._monitor_tasks
    assert patched_logger.error.called  # loud, monitor-specific


async def test_clean_monitor_exit_clears_handle_without_error(patched_logger):
    gw = _Gw()

    async def clean_exit():
        return  # a monitor that decides to stop itself (e.g. no webcam hardware)

    task = gw._spawn_monitor_task("webcam", clean_exit())
    await asyncio.gather(task, return_exceptions=True)
    await _drain()

    assert "webcam" not in gw._monitor_tasks  # still cleared → truthful
    assert not patched_logger.error.called    # a clean self-stop is not an error
    assert patched_logger.info.called


async def test_stale_monitor_callback_does_not_clobber_a_replacement(patched_logger):
    """A late done-callback from an OLD monitor task must not clear a NEWER handle
    that a stop→start already installed under the same name."""
    gw = _Gw()

    async def boom():
        raise RuntimeError("old died")

    old = asyncio.ensure_future(boom())
    await asyncio.gather(old, return_exceptions=True)

    gw._monitor_tasks["webcam"] = "installed"  # a newer handle owns the slot now
    gw._on_monitor_task_done("webcam", old)     # stale callback for the dead old task

    assert gw._monitor_tasks["webcam"] == "installed"  # untouched


# ── is_monitor_running: the honest "actually live?" probe the deck card reads ──


def test_is_monitor_running_false_when_absent():
    """A never-started (or crashed-then-cleared) monitor reads not-running."""
    gw = _Gw()
    assert gw.is_monitor_running("webcam") is False


async def test_is_monitor_running_true_for_a_live_task():
    gw = _Gw()

    async def loop_forever():
        await asyncio.sleep(30)

    task = gw._spawn_monitor_task("webcam", loop_forever())
    try:
        assert gw.is_monitor_running("webcam") is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_is_monitor_running_false_for_a_finished_task():
    """A monitor whose task finished (crash) reads not-running even if, in a race,
    the clearing callback hasn't run yet — the handle is done."""
    gw = _Gw()

    async def boom():
        raise RuntimeError("crashed")

    # bypass _spawn_monitor_task's clearing callback to isolate the done-task case
    task = asyncio.ensure_future(boom())
    await asyncio.gather(task, return_exceptions=True)
    gw._monitor_tasks["webcam"] = task  # a done task still parked in the map
    assert gw.is_monitor_running("webcam") is False


def test_is_monitor_running_true_for_installed_producer_marker():
    """self_errors / config_incidents store the string 'installed'; connectivity 'live'."""
    gw = _Gw()
    gw._monitor_tasks["config_incidents"] = "installed"
    gw._monitor_tasks["connectivity"] = "live"
    assert gw.is_monitor_running("config_incidents") is True
    assert gw.is_monitor_running("connectivity") is True
