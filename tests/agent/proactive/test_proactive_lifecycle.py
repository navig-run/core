"""ProactiveEngine.start() must be idempotent and must not accumulate hooks.

Two defects fixed:
- ``start()`` had no re-entrancy guard → two near-simultaneous ``POST /proactive/start``
  (deck double-click, or deck + OS) each schedule ``start()`` → two concurrent polling loops.
- ``start()`` calls ``register_hook("proactive:check", self.run_checks)`` every time and the
  registry appends without de-duping, while ``stop()`` never unregistered → a start→stop→start
  cycle left TWO ``run_checks`` handlers, so every trigger ran the calendar/email scan twice.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from navig.agent.proactive.engine import ProactiveEngine
from navig.core import hooks as hooks_mod

pytestmark = pytest.mark.unit


async def _noop(event=None):
    return None


async def _spin_up(eng: ProactiveEngine) -> asyncio.Task:
    """Launch start() (it blocks in a poll loop) and let it register + reach its first await."""
    task = asyncio.create_task(eng.start())
    for _ in range(5):
        await asyncio.sleep(0)
    return task


async def _tear_down(eng: ProactiveEngine, task: asyncio.Task) -> None:
    await eng.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_start_stop_start_does_not_accumulate_hooks(monkeypatch):
    hooks_mod.clear_hooks("proactive:check")
    eng = ProactiveEngine()
    monkeypatch.setattr(eng, "run_checks", _noop)

    # First lifecycle.
    task = await _spin_up(eng)
    assert len(hooks_mod._registry.get_handlers("proactive:check")) == 1
    await _tear_down(eng, task)
    # stop() unregistered — back to zero.
    assert len(hooks_mod._registry.get_handlers("proactive:check")) == 0

    # Restart: exactly ONE handler, never two (the accumulation bug).
    task = await _spin_up(eng)
    assert len(hooks_mod._registry.get_handlers("proactive:check")) == 1
    await _tear_down(eng, task)
    assert len(hooks_mod._registry.get_handlers("proactive:check")) == 0


async def test_second_start_is_idempotent_not_a_second_loop(monkeypatch):
    hooks_mod.clear_hooks("proactive:check")
    eng = ProactiveEngine()
    monkeypatch.setattr(eng, "run_checks", _noop)

    task = await _spin_up(eng)
    assert eng.running is True
    # A second start() while running must return immediately (not block on a 2nd poll loop)
    # and must not add a second hook handler.
    await asyncio.wait_for(eng.start(), timeout=1.0)
    assert len(hooks_mod._registry.get_handlers("proactive:check")) == 1

    await _tear_down(eng, task)
