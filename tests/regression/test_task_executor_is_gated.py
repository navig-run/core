"""NAVIG's fourth tool dispatcher ran shell commands with no interlock.

`TaskExecutor._execute_step` dispatches a plan step through `ActionRegistry` and, when
the action is not one of those, falls through to **`ToolRouter`** — a second, entirely
separate tool registry. That router has no approval gate: `gate_agent_tool_call` appears
nowhere in `navig/tools/router.py`, and its only protection is `safety_mode == "strict"`
while the default is `"standard"`.

`ToolRouter`'s `exec_pack` registers **`bash_exec`** — "Execute a shell command" — with
`SafetyLevel.DANGEROUS` and a live handler. So the same tool name was held for approval
through the agent registry and ran unprompted through this one.

Reachable from `ConversationalAgent.chat()`: when `register_all_tools()` fails or no
tools are configured, `_agentic_tools_registered` stays False and the fallback branch
extracts a plan from the model's reply and calls `execute_plan`.

`execute_plan` does honour a `confirmation_needed` flag — but that flag comes from the
**plan**, i.e. the model decides whether its own plan needs a human. A gate the gated
party can switch off is not a gate.

Three earlier PRs closed this exact class on the other dispatchers; the guard that was
supposed to prevent a fourth keys on `_AGENT_REGISTRY.dispatch` and could not see a
second tool system. It now derives from both.
"""

from __future__ import annotations

import pytest

from navig.agent.conv.executor import ExecutionStep, Task, TaskExecutor, TaskStatus
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
    """Record everything that actually reaches a dispatcher."""
    calls: list[str] = []

    class _Registry:
        @staticmethod
        async def dispatch(action, params):
            calls.append(action)
            return True, "ok"

    monkeypatch.setattr(
        "navig.agent.action_registry.get_action_registry", lambda: _Registry()
    )
    return calls


def _task(*actions: str) -> Task:
    return Task(
        id="t1",
        goal="g",
        status=TaskStatus.EXECUTING,
        plan=[ExecutionStep(action=a, description=a, params={"command": "ls"}) for a in actions],
    )


def _deny():
    async def _d(_req) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _d


def _approve():
    async def _a(_req) -> ApprovalDecision:
        return ApprovalDecision.APPROVED

    get_approval_gate().backend = _a


async def test_a_denied_step_does_not_dispatch(dispatched) -> None:
    _deny()
    task = _task("bash_exec")

    await TaskExecutor(max_attempts=3).execute(task)

    assert dispatched == [], "a shell command ran despite the operator denying it"
    assert task.status is TaskStatus.FAILED


async def test_an_approved_step_still_runs(dispatched) -> None:
    """The interlock must not become a wall."""
    _approve()
    task = _task("bash_exec")

    await TaskExecutor(max_attempts=3).execute(task)

    assert dispatched == ["bash_exec"]
    assert task.status is TaskStatus.SUCCESS


async def test_a_read_only_action_is_not_gated(dispatched) -> None:
    """Gating reads trains the operator to click through prompts."""

    async def _explode(_req) -> ApprovalDecision:
        raise AssertionError("read_file must not reach the approval backend")

    get_approval_gate().backend = _explode

    await TaskExecutor(max_attempts=3).execute(_task("read_file"))

    assert dispatched == ["read_file"]


async def test_a_denial_is_not_retried(dispatched) -> None:
    """`execute` retries a failed step up to `_max_attempts`. Re-asking a human who
    already said no — three times, with a backoff — is how they learn to click yes."""
    asked: list[int] = []

    async def _count(_req) -> ApprovalDecision:
        asked.append(1)
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _count

    await TaskExecutor(max_attempts=3).execute(_task("bash_exec"))

    assert len(asked) == 1, f"the operator was asked {len(asked)} times for one refusal"
    assert dispatched == []


async def test_a_denial_stops_the_plan(dispatched) -> None:
    """Later steps were planned assuming the earlier one ran."""
    _deny()
    task = _task("bash_exec", "read_file")

    await TaskExecutor(max_attempts=3).execute(task)

    assert dispatched == []
    assert task.status is TaskStatus.FAILED


async def test_a_broken_interlock_denies(dispatched, monkeypatch) -> None:
    """Fail closed: a gate that raises must not become a gate that waves things through."""

    async def _boom(*a, **k):
        raise RuntimeError("gate on fire")

    monkeypatch.setattr("navig.tools.approval.gate_agent_tool_call", _boom)

    await TaskExecutor(max_attempts=3).execute(_task("bash_exec"))

    assert dispatched == []


async def test_an_external_mcp_action_in_a_plan_is_gated(dispatched) -> None:
    """A plan is a place a third-party tool name can appear."""
    _deny()

    await TaskExecutor(max_attempts=3).execute(_task("mcp__acme__delete_everything"))

    assert dispatched == []


async def test_the_executor_still_returns_a_string_on_denial(dispatched) -> None:
    """`execute()`'s documented guarantee: always returns a string, never propagates."""
    _deny()

    result = await TaskExecutor(max_attempts=3).execute(_task("bash_exec"))

    assert isinstance(result, str)


async def test_the_router_fallthrough_is_gated_too(monkeypatch) -> None:
    """The dangerous half: an action ActionRegistry does not know falls through to
    ToolRouter, where `bash_exec` is registered DANGEROUS with a live handler."""
    routed: list[str] = []

    class _Registry:
        @staticmethod
        async def dispatch(action, params):
            return False, None  # not an ActionRegistry action → fall through

    class _Router:
        @staticmethod
        async def async_execute(action):
            routed.append(action.tool)
            raise AssertionError("the ToolRouter fallthrough ran without approval")

    monkeypatch.setattr(
        "navig.agent.action_registry.get_action_registry", lambda: _Registry()
    )
    monkeypatch.setattr("navig.tools.router.get_tool_router", lambda: _Router())
    _deny()

    await TaskExecutor(max_attempts=3).execute(_task("bash_exec"))

    assert routed == []
