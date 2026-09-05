"""A tool that RAN and FAILED does not raise — every consumer must read the marker.

`AgentToolRegistry.dispatch` raises for an unknown tool, a blocked permission and
an unavailable `check_fn`. It does **not** raise when the tool itself failed:
`_result_to_str` turns `ToolResult(success=False)` into ``"[ERROR] …"`` and
returns it normally. `navig_run` with a non-zero exit, a failed db query or dump
and a permission-denied write all arrive that way.

Only `conv/agent.py` knew that, and it kept a private copy of the prefix list, so
both programmatic consumers misread it:

* `plan_execute.py` set ``step.status = "success"`` unconditionally. So
  ``navig agent plan "restart nginx and verify it's healthy"`` rendered
  ``✅ Step 1 — navig_run (success)`` over a failed restart — a false green on a
  live-infra surface — and because plan *revision* only ran from the ``except``
  arm, recovery never fired for the one failure mode that does not raise.
* `speculative.py` cached the string. An ``[ERROR] …`` was then served back as a
  speculative HIT for the cache's whole TTL: one transient blip pinned as a
  permanent answer the agent never retried.

The prefix list now lives beside the code that produces it, as
`agent_tool_registry.is_failure_result`.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

# NOTE: `is_failure_result` / `TOOL_FAILURE_PREFIXES` are imported INSIDE the test
# bodies, not here. A module-level import of new machinery makes the whole file
# uncollectable on the pre-fix commit, so the teeth test yields an ImportError
# instead of the behavioural failures that prove the bug — the new-machinery
# guards and the behavioural regressions have to be able to fail separately.


# ── the shared predicate ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "[ERROR] navig_run exited 1",
        "[Tool error: boom]",
        "[Denied: approval interlock unavailable for 'x']",
        "[Verification blocked: unsafe]",
    ],
)
def test_every_failure_marker_is_recognised(value: str) -> None:
    from navig.agent.agent_tool_registry import is_failure_result

    assert is_failure_result(value)


@pytest.mark.parametrize("value", ["ok", "", "  [ERROR] leading space", None, 123, {"a": 1}])
def test_a_success_or_non_string_is_not_a_failure(value: object) -> None:
    """Anti-vacuity partner. Note a LEADING SPACE is deliberately not a failure —
    the markers are emitted at position 0, and loosening this would swallow real
    tool output that merely mentions an error."""
    from navig.agent.agent_tool_registry import is_failure_result

    assert not is_failure_result(value)


def test_the_marker_list_is_not_empty() -> None:
    """If someone empties the tuple every check silently passes."""
    from navig.agent.agent_tool_registry import TOOL_FAILURE_PREFIXES

    assert TOOL_FAILURE_PREFIXES
    assert "[ERROR" in TOOL_FAILURE_PREFIXES


# ── plan_execute ────────────────────────────────────────────────────────────


def _plan_with_one_step(tool: str = "navig_run"):
    from navig.agent.plan_execute import ExecutionPlan, PlanStep

    step = PlanStep(tool=tool, args={"command": "systemctl restart nginx"})
    return ExecutionPlan(task="restart nginx", steps=[step]), step


def _executor(dispatch_result: str):
    from navig.agent.plan_execute import PlanExecuteAgent

    agent = PlanExecuteAgent(MagicMock())
    registry = MagicMock()
    registry.dispatch = MagicMock(return_value=dispatch_result)
    return agent, registry


async def test_a_failed_step_is_not_reported_as_success(monkeypatch) -> None:
    agent, registry = _executor("[ERROR] systemctl: exit 1")
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)

    plan, step = _plan_with_one_step()
    await agent._execute(plan, toolset=["core"], max_retries=0)

    assert step.status == "failed", (
        "a green tick over a failed live-infra step is the false-green NAVIG's own "
        "doctor-honesty doctrine forbids"
    )
    assert step.error and "exit 1" in step.error


async def test_a_successful_step_is_still_reported_as_success(monkeypatch) -> None:
    """Anti-vacuity partner: the happy path must be untouched."""
    agent, registry = _executor("nginx restarted")
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)

    plan, step = _plan_with_one_step()
    await agent._execute(plan, toolset=["core"], max_retries=0)

    assert step.status == "success"
    assert not step.error
    assert step.output == "nginx restarted"


async def test_a_non_raising_failure_now_triggers_plan_revision(monkeypatch) -> None:
    """The recovery machinery hung off the `except` arm, so it had never run for
    the failure mode that does not raise."""
    from navig.agent.plan_execute import PlanStep

    agent, registry = _executor("[ERROR] systemctl: exit 1")
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)

    revised_calls: list[int] = []

    async def _fake_revise(failed_step_num, failed_step, remaining_steps, toolset):
        revised_calls.append(failed_step_num)
        return []

    monkeypatch.setattr(agent, "_revise_plan", _fake_revise)

    from navig.agent.plan_execute import ExecutionPlan

    plan = ExecutionPlan(
        task="restart nginx",
        steps=[
            PlanStep(tool="navig_run", args={"command": "systemctl restart nginx"}),
            PlanStep(tool="navig_run", args={"command": "curl localhost"}),
        ],
    )
    await agent._execute(plan, toolset=["core"], max_retries=1)

    assert revised_calls == [1], (
        "a failed first step must give the planner a chance to revise the rest"
    )


# ── speculative ─────────────────────────────────────────────────────────────


def _spec_executor(dispatch_result: str):
    from navig.agent.speculative import SpeculativeExecutor

    return SpeculativeExecutor(lambda tool, args: dispatch_result, config={"enabled": True})


async def test_a_failed_speculation_is_not_cached() -> None:
    from navig.agent.speculative import Prediction

    spec = _spec_executor("[ERROR] connection refused")
    pred = Prediction(tool="navig_db_query", args={"q": "select 1"}, confidence=0.9)

    await spec._speculative_run(pred)

    assert spec.cache.get(pred.tool, pred.args) is None, (
        "caching a failure serves it back as a speculative HIT for the whole TTL — "
        "one blip pinned as a permanent answer the agent never retries"
    )


async def test_a_successful_speculation_is_still_cached() -> None:
    """Anti-vacuity partner: speculation must still do its job."""
    from navig.agent.speculative import Prediction

    spec = _spec_executor("row count: 42")
    pred = Prediction(tool="navig_db_query", args={"q": "select 1"}, confidence=0.9)

    await spec._speculative_run(pred)

    assert spec.cache.get(pred.tool, pred.args) == "row count: 42"


def test_asyncio_is_importable_for_these_tests() -> None:
    """Guard against an unused-import cleanup breaking the async fixtures."""
    assert asyncio is not None
