"""Shared fixtures for agent tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_speculative_executor():
    """Isolate the process-wide speculative-executor singleton between tests.

    ``run_agentic`` no longer resets the executor per turn (that raced concurrent
    turns and defeated the cross-turn cache — see agent teardown), so a test that
    creates the executor with its own mocked ``dispatch_fn`` would otherwise leak
    it into the next test and corrupt tool dispatch. Reset before AND after each
    test to keep them independent of run order.
    """
    def _reset():
        try:
            from navig.agent.speculative import reset_speculative_executor
            reset_speculative_executor()
        except Exception:  # noqa: BLE001
            pass

    _reset()
    yield
    _reset()
