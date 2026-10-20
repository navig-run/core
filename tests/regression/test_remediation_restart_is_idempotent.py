"""A persistent fault must not pile up duplicate restart actions.

`RemediationEngine.schedule_restart_sync` mints an action keyed by
`component_<timestamp>`. The health loop re-evaluates a still-failed component every
cycle (~60s) and the restart runs later in the process loop, so before this fix each
cycle enqueued a brand-new COMPONENT_RESTART for the SAME component — each retried
independently with its own backoff. Scheduling is now idempotent per component while a
restart is still PENDING or IN_PROGRESS.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _engine(tmp_path):
    from navig.agent.remediation import RemediationEngine

    return RemediationEngine(config_dir=tmp_path, log_dir=tmp_path / "logs")


def test_repeat_schedule_while_pending_returns_the_same_action(tmp_path):
    engine = _engine(tmp_path)

    first = engine.schedule_restart_sync("gateway", "health check failed")
    second = engine.schedule_restart_sync("gateway", "health check failed again")

    assert first == second, "a second schedule for a pending component minted a new action"
    restarts = [
        a
        for a in engine._actions.values()
        if a.component == "gateway" and a.type.value == "component_restart"
    ]
    assert len(restarts) == 1, f"expected one restart action, got {len(restarts)}"


def test_different_components_are_independent(tmp_path):
    engine = _engine(tmp_path)

    a = engine.schedule_restart_sync("gateway", "down")
    b = engine.schedule_restart_sync("browser", "down")

    assert a != b, "two different components collapsed into one action"
    assert len({a, b}) == 2


def test_a_terminal_action_does_not_block_a_new_restart(tmp_path):
    """SUCCESS/FAILED/SKIPPED are terminal — a genuinely new fault must schedule again."""
    from navig.agent.remediation import RemediationStatus

    engine = _engine(tmp_path)
    first = engine.schedule_restart_sync("gateway", "down")
    engine._actions[first].status = RemediationStatus.SUCCESS

    second = engine.schedule_restart_sync("gateway", "down again")
    assert second != first, "a completed restart wrongly blocked a new one"
