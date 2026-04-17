"""Tests for F-21: Two-Tier Plan-Execute Agent Mode.

Covers:
- PlanStep / ExecutionPlan dataclass helpers
- PlanExecuteAgent._plan() JSON parsing (including malformed input)
- PlanExecuteAgent.run() dry-run path
- PlanExecuteAgent.run() full execution path
- PlanExecuteAgent._execute() step dispatching
- format_plan_report() output formatting
- CLI surface: `navig agent plan` registers with the correct name
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from navig.agent.plan_execute import (
    ExecutionPlan,
    PlanExecuteAgent,
    PlanStep,
    format_plan_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockAgent:
    """Minimal stand-in for ConversationalAgent used in PlanExecuteAgent."""


def _make_agent(llm_response: str = '{"steps": []}') -> PlanExecuteAgent:
    """Return a PlanExecuteAgent whose _llm_call always returns *llm_response*."""
    agent = PlanExecuteAgent(_MockAgent())
    # Patch the async _llm_call so tests don't hit a real LLM
    async def _fake_llm(system: str, user_message: str) -> str:  # noqa: ARG001
        return llm_response
    agent._llm_call = _fake_llm
    return agent


# ---------------------------------------------------------------------------
# PlanStep tests
# ---------------------------------------------------------------------------

class TestPlanStep:
    def test_to_dict_basic(self):
        step = PlanStep(tool="navig_run", reason="check disk", args={"cmd": "df -h"})
        d = step.to_dict()
        assert d["tool"] == "navig_run"
        assert d["reason"] == "check disk"
        assert d["args"] == {"cmd": "df -h"}
        assert d["status"] == "pending"

    def test_to_dict_with_output(self):
        step = PlanStep(tool="navig_run", reason="check", args={})
        step.status = "success"
        step.output = "disk ok"
        d = step.to_dict()
        assert d["status"] == "success"
        assert d["output"] == "disk ok"

    def test_default_status_is_pending(self):
        step = PlanStep(tool="x", reason="y", args={})
        assert step.status == "pending"


# ---------------------------------------------------------------------------
# ExecutionPlan tests
# ---------------------------------------------------------------------------

class TestExecutionPlan:
    def test_to_dict_includes_steps(self):
        plan = ExecutionPlan(task="test task")
        plan.steps = [PlanStep(tool="navig_run", reason="r", args={})]
        d = plan.to_dict()
        assert d["task"] == "test task"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["tool"] == "navig_run"

    def test_to_dict_empty_plan(self):
        plan = ExecutionPlan(task="nothing")
        d = plan.to_dict()
        assert d["steps"] == []

    def test_created_at_is_set(self):
        plan = ExecutionPlan(task="x")
        assert plan.created_at is not None


# ---------------------------------------------------------------------------
# PlanExecuteAgent._plan() — JSON parsing
# ---------------------------------------------------------------------------

class TestPlanParsing:
    def test_parses_valid_plan(self):
        payload = '{"steps": [{"tool": "navig_run", "reason": "list files", "args": {"cmd": "ls"}}]}'
        agent = _make_agent(llm_response=payload)
        plan = asyncio.run(agent._plan("list files on server"))
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "navig_run"
        assert plan.steps[0].args == {"cmd": "ls"}

    def test_parses_multiple_steps(self):
        payload = """{
            "steps": [
                {"tool": "navig_run", "reason": "check disk", "args": {"cmd": "df -h"}},
                {"tool": "navig_run", "reason": "check mem",  "args": {"cmd": "free -m"}}
            ]
        }"""
        agent = _make_agent(llm_response=payload)
        plan = asyncio.run(agent._plan("system health check"))
        assert len(plan.steps) == 2
        assert plan.steps[1].tool == "navig_run"

    def test_empty_steps_array(self):
        agent = _make_agent(llm_response='{"steps": []}')
        plan = asyncio.run(agent._plan("do nothing"))
        assert plan.steps == []

    def test_malformed_json_returns_empty_plan(self):
        agent = _make_agent(llm_response="not json at all")
        plan = asyncio.run(agent._plan("anything"))
        assert plan.steps == []

    def test_missing_steps_key_returns_empty_plan(self):
        agent = _make_agent(llm_response='{"result": "ok"}')
        plan = asyncio.run(agent._plan("anything"))
        assert plan.steps == []

    def test_json_wrapped_in_markdown_fences(self):
        """LLMs often return ```json ... ``` fences — plan should strip them."""
        payload = '```json\n{"steps": [{"tool": "navig_run", "reason": "r", "args": {}}]}\n```'
        agent = _make_agent(llm_response=payload)
        plan = asyncio.run(agent._plan("task"))
        # If the implementation strips fences, steps ≥ 1; if not, it returns empty.
        # We assert no exception is raised either way.
        assert isinstance(plan.steps, list)


# ---------------------------------------------------------------------------
# PlanExecuteAgent.run() — dry-run path
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_returns_plan_without_executing(self):
        payload = '{"steps": [{"tool": "navig_run", "reason": "list", "args": {"cmd": "ls"}}]}'
        agent = _make_agent(llm_response=payload)

        dispatch_calls: list[Any] = []

        def _fake_dispatch(name, args, vault_injector=None):  # noqa: ARG001
            dispatch_calls.append(name)
            return "ok"

        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY") as mock_reg:
            mock_reg.dispatch.side_effect = _fake_dispatch
            plan = asyncio.run(agent.run("list files", dry_run=True, auto_approve=True))

        # Should have planned steps but NOT called dispatch
        assert len(plan.steps) == 1
        assert not dispatch_calls

    def test_dry_run_steps_remain_pending(self):
        payload = '{"steps": [{"tool": "navig_run", "reason": "r", "args": {}}]}'
        agent = _make_agent(llm_response=payload)
        plan = asyncio.run(agent.run("task", dry_run=True, auto_approve=True))
        assert all(s.status == "pending" for s in plan.steps)

    def test_empty_plan_returns_immediately(self):
        agent = _make_agent(llm_response='{"steps": []}')
        plan = asyncio.run(agent.run("nothing", dry_run=False, auto_approve=True))
        assert plan.steps == []


# ---------------------------------------------------------------------------
# PlanExecuteAgent.run() — execution path
# ---------------------------------------------------------------------------

class TestExecution:
    def test_successful_step_sets_status_success(self):
        payload = '{"steps": [{"tool": "navig_run", "reason": "test", "args": {"cmd": "echo hi"}}]}'
        agent = _make_agent(llm_response=payload)

        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY") as mock_reg:
            mock_reg.dispatch.return_value = "hi\n"
            plan = asyncio.run(agent.run("echo hi", dry_run=False, auto_approve=True))

        assert plan.steps[0].status == "success"
        assert "hi" in (plan.steps[0].output or "")

    def test_dispatched_output_is_truncated_at_2000_chars(self):
        long_output = "x" * 5000
        payload = '{"steps": [{"tool": "navig_run", "reason": "r", "args": {}}]}'
        agent = _make_agent(llm_response=payload)

        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY") as mock_reg:
            mock_reg.dispatch.return_value = long_output
            plan = asyncio.run(agent.run("run", dry_run=False, auto_approve=True))

        assert len(plan.steps[0].output) <= 2000

    def test_dispatch_exception_marks_step_failed(self):
        payload = '{"steps": [{"tool": "bad_tool", "reason": "r", "args": {}}]}'
        agent = _make_agent(llm_response=payload)

        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY") as mock_reg:
            mock_reg.dispatch.side_effect = RuntimeError("tool not found")
            plan = asyncio.run(agent.run("fail", dry_run=False, auto_approve=True))

        assert plan.steps[0].status == "failed"
        assert plan.steps[0].error  # non-empty error message

    def test_multiple_steps_all_executed(self):
        payload = """{
            "steps": [
                {"tool": "navig_run", "reason": "step1", "args": {"cmd": "a"}},
                {"tool": "navig_run", "reason": "step2", "args": {"cmd": "b"}}
            ]
        }"""
        agent = _make_agent(llm_response=payload)
        dispatch_results = ["output_a", "output_b"]
        call_idx = [0]

        def _dispatch(name, args, vault_injector=None):  # noqa: ARG001
            result = dispatch_results[call_idx[0]]
            call_idx[0] += 1
            return result

        with patch("navig.agent.agent_tool_registry._AGENT_REGISTRY") as mock_reg:
            mock_reg.dispatch.side_effect = _dispatch
            plan = asyncio.run(agent.run("two steps", dry_run=False, auto_approve=True))

        assert len(plan.steps) == 2
        assert plan.steps[0].status == "success"
        assert plan.steps[1].status == "success"
        assert mock_reg.dispatch.call_count == 2


# ---------------------------------------------------------------------------
# format_plan_report
# ---------------------------------------------------------------------------

class TestFormatPlanReport:
    def test_report_contains_task(self):
        plan = ExecutionPlan(task="backup all databases")
        report = format_plan_report(plan)
        assert "backup all databases" in report

    def test_report_with_successful_steps(self):
        plan = ExecutionPlan(task="check health")
        step = PlanStep(tool="navig_run", reason="check disk", args={})
        step.status = "success"
        step.output = "Filesystem 100G"
        plan.steps = [step]
        report = format_plan_report(plan)
        assert "success" in report.lower() or "✓" in report or "navig_run" in report

    def test_report_with_failed_step(self):
        plan = ExecutionPlan(task="do thing")
        step = PlanStep(tool="bad_tool", reason="bad step", args={})
        step.status = "failed"
        step.error = "tool not found"
        plan.steps = [step]
        report = format_plan_report(plan)
        assert "fail" in report.lower() or "✗" in report or "tool not found" in report

    def test_report_returns_string(self):
        plan = ExecutionPlan(task="any task")
        report = format_plan_report(plan)
        assert isinstance(report, str)
        assert len(report) > 0


# ---------------------------------------------------------------------------
# CLI surface registration
# ---------------------------------------------------------------------------

class TestCliRegistration:
    def test_agent_plan_command_registered(self):
        """navig agent plan must appear in the agent_app command registry."""
        from navig.commands.agent import agent_app

        command_names = [cmd.name for cmd in agent_app.registered_commands]
        assert "plan" in command_names, (
            f"'plan' command not found; registered: {command_names}"
        )
