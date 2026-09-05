"""`navig agent plan` ran every tool with no interlock, and approved itself.

Three defects in one path, and the third is the one that makes the other two matter:

1. `_execute` dispatched straight into `_AGENT_REGISTRY.dispatch` — `gate_agent_tool_call`
   appeared **zero** times in the file. NAVIG has three tool dispatchers; the two agent
   editions were gated and this one never was.
2. `_request_approval` returned ``True`` when ``not sys.stdin.isatty()``. Every
   daemon-hosted caller is non-interactive by definition, so the one environment with
   nobody watching was the one that approved itself.
3. `_request_approval` also returned ``True`` from a bare ``except``. A failure to *ask*
   is not an answer.

And the reason a whole-plan prompt could never have been the safety boundary anyway:
**`_revise_plan` lets the LLM replace the remaining steps mid-run.** The operator
approves plan A; plan B executes. Only a per-dispatch gate covers that, and it does so
for free because revised steps re-enter the same loop.

`core/tests/approval/test_gate_manager_wiring.py` greps the two agent editions for
`gate_agent_tool_call`; this third dispatcher was outside its scope.
"""

from __future__ import annotations

import pytest

from navig.agent.plan_execute import ExecutionPlan, PlanExecuteAgent, PlanStep
from navig.tools.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    get_approval_gate,
    set_approval_policy,
)


@pytest.fixture(autouse=True)
def _default_policy():
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)
    yield
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)


@pytest.fixture
def dispatched(monkeypatch) -> list[str]:
    """Record every tool the executor actually dispatches."""
    calls: list[str] = []

    def _fake_dispatch(name, args, injector=None):
        calls.append(name)
        return "ok"

    from navig.agent import agent_tool_registry

    monkeypatch.setattr(agent_tool_registry._AGENT_REGISTRY, "dispatch", _fake_dispatch)
    return calls


def _plan(*tools: str) -> ExecutionPlan:
    return ExecutionPlan(
        task="t", steps=[PlanStep(tool=t, args={"x": 1}, reason="r") for t in tools]
    )


def _agent() -> PlanExecuteAgent:
    class _Minimal:
        pass

    return PlanExecuteAgent(_Minimal())


# ---------------------------------------------------------------------------
# The interlock
# ---------------------------------------------------------------------------


async def test_a_denied_tool_does_not_run(dispatched) -> None:
    async def _deny(_req) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _deny
    plan = _plan("bash_exec")

    await _agent()._execute(plan)

    assert dispatched == [], "A destructive tool ran despite the operator denying it."
    assert plan.steps[0].status == "denied"


async def test_an_approved_tool_still_runs(dispatched) -> None:
    """The interlock must not become a wall."""

    async def _approve(_req) -> ApprovalDecision:
        return ApprovalDecision.APPROVED

    get_approval_gate().backend = _approve
    plan = _plan("bash_exec")

    await _agent()._execute(plan)

    assert dispatched == ["bash_exec"]
    assert plan.steps[0].status == "success"


async def test_a_read_only_tool_is_not_gated(dispatched) -> None:
    """Gating reads trains the operator to click through prompts."""

    async def _explode(_req) -> ApprovalDecision:
        raise AssertionError("read_file must not reach the approval backend")

    get_approval_gate().backend = _explode
    plan = _plan("read_file")

    await _agent()._execute(plan)

    assert dispatched == ["read_file"]


async def test_a_denial_stops_the_plan_rather_than_half_applying_it(dispatched) -> None:
    """Later steps were planned assuming the earlier one ran."""

    async def _deny(_req) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _deny
    plan = _plan("bash_exec", "read_file", "write_file")

    await _agent()._execute(plan)

    assert dispatched == []
    assert [s.status for s in plan.steps] == ["denied", "skipped", "skipped"]


async def test_a_broken_interlock_denies(dispatched, monkeypatch) -> None:
    """Fail closed: a gate that raises must not become a gate that waves things through."""

    async def _boom(*a, **k):
        raise RuntimeError("gate on fire")

    monkeypatch.setattr("navig.tools.approval.gate_agent_tool_call", _boom)
    plan = _plan("bash_exec")

    await _agent()._execute(plan)

    assert dispatched == []
    assert plan.steps[0].status == "denied"


async def test_an_external_mcp_tool_in_a_plan_is_gated(dispatched) -> None:
    """The plan path must honour the external-tool default too, not just
    DESTRUCTIVE_TOOLS — a plan is a place a third-party tool name can appear."""

    async def _deny(_req) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _deny
    plan = _plan("mcp__acme__delete_everything")

    await _agent()._execute(plan)

    assert dispatched == []
    assert plan.steps[0].status == "denied"


# ---------------------------------------------------------------------------
# The whole-plan prompt fails closed
# ---------------------------------------------------------------------------


async def test_non_interactive_does_not_self_approve(monkeypatch) -> None:
    """The daemon is never a TTY, so this branch was the whole gateway."""
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: False)})())

    assert await _agent()._request_approval(_plan("bash_exec")) is False


async def test_a_failure_to_ask_is_not_approval(monkeypatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("console on fire")

    monkeypatch.setattr("navig.console_helper.info", _boom)

    assert await _agent()._request_approval(_plan("bash_exec")) is False


async def test_a_cancelled_prompt_is_not_approval(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))

    assert await _agent()._request_approval(_plan("bash_exec")) is False


@pytest.mark.parametrize("answer,expected", [("y", True), ("yes", True), ("", False), ("n", False)])
async def test_an_interactive_answer_is_honoured(monkeypatch, answer, expected) -> None:
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr("builtins.input", lambda *a: answer)

    assert await _agent()._request_approval(_plan("bash_exec")) is expected
