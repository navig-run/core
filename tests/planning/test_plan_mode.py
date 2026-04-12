"""Tests for FA-01: Plan Mode — PlanState, PlanStep, PlanSession, PlanInterceptor, plan tools."""

from __future__ import annotations

import asyncio
import time

import pytest

from navig.agent.plan_mode import PlanInterceptor, PlanSession, PlanState, PlanStep

pytestmark = pytest.mark.integration

# ─────────────────────────────────────────────────────────────
# PlanState enum
# ─────────────────────────────────────────────────────────────


class TestPlanState:
    def test_all_states_present(self):
        names = {s.name for s in PlanState}
        assert names == {"INACTIVE", "PLANNING", "REVIEWING", "EXECUTING", "COMPLETED"}

    def test_values_are_strings(self):
        for s in PlanState:
            assert isinstance(s.value, str)


# ─────────────────────────────────────────────────────────────
# PlanStep
# ─────────────────────────────────────────────────────────────


class TestPlanStep:
    def test_defaults(self):
        step = PlanStep(description="Fix typo")
        assert step.description == "Fix typo"
        assert step.tool_calls == []
        assert step.files_affected == []
        assert step.risk_level == "low"
        assert step.status == "pending"

    def test_all_fields(self):
        step = PlanStep(
            description="Refactor module",
            tool_calls=["write_file", "bash_exec"],
            files_affected=["src/app.py"],
            risk_level="high",
            status="done",
        )
        assert step.risk_level == "high"
        assert step.status == "done"
        assert len(step.tool_calls) == 2

    def test_invalid_risk_raises(self):
        with pytest.raises(ValueError, match="risk_level"):
            PlanStep(description="x", risk_level="critical")

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            PlanStep(description="x", status="running")

    def test_valid_risks(self):
        for risk in ("low", "medium", "high"):
            step = PlanStep(description="x", risk_level=risk)
            assert step.risk_level == risk

    def test_valid_statuses(self):
        for st in ("pending", "in_progress", "done", "skipped"):
            step = PlanStep(description="x", status=st)
            assert step.status == st


# ─────────────────────────────────────────────────────────────
# PlanSession
# ─────────────────────────────────────────────────────────────


class TestPlanSession:
    def test_default_session(self):
        s = PlanSession()
        assert s.state == PlanState.INACTIVE
        assert s.steps == []
        assert s.context_gathered == []
        assert s.approved_at is None
        assert len(s.plan_id) == 12

    def test_is_active_inactive(self):
        s = PlanSession(state=PlanState.INACTIVE)
        assert not s.is_active

    def test_is_active_planning(self):
        s = PlanSession(state=PlanState.PLANNING)
        assert s.is_active

    def test_is_active_reviewing(self):
        s = PlanSession(state=PlanState.REVIEWING)
        assert s.is_active

    def test_is_active_executing(self):
        s = PlanSession(state=PlanState.EXECUTING)
        assert s.is_active

    def test_is_active_completed(self):
        s = PlanSession(state=PlanState.COMPLETED)
        assert not s.is_active

    def test_step_count(self):
        s = PlanSession(steps=[PlanStep("a"), PlanStep("b")])
        assert s.step_count == 2

    def test_pending_steps(self):
        s = PlanSession(steps=[PlanStep("a"), PlanStep("b", status="done"), PlanStep("c")])
        assert len(s.pending_steps) == 2

    def test_files_at_risk_deduped_sorted(self):
        s = PlanSession(
            steps=[
                PlanStep("x", files_affected=["b.py", "a.py"]),
                PlanStep("y", files_affected=["a.py", "c.py"]),
            ]
        )
        assert s.files_at_risk == ["a.py", "b.py", "c.py"]

    def test_summary_keys(self):
        s = PlanSession(state=PlanState.PLANNING)
        sm = s.summary()
        assert set(sm.keys()) == {
            "plan_id",
            "state",
            "total_steps",
            "pending",
            "files_affected",
            "created_at",
            "approved_at",
        }
        assert sm["state"] == "planning"


# ─────────────────────────────────────────────────────────────
# PlanInterceptor — lifecycle
# ─────────────────────────────────────────────────────────────


class TestInterceptorLifecycle:
    def test_initial_state_inactive(self):
        pi = PlanInterceptor()
        assert pi.state == PlanState.INACTIVE
        assert not pi.is_planning
        assert not pi.is_active

    def test_start_enters_planning(self):
        pi = PlanInterceptor()
        session = pi.start()
        assert pi.state == PlanState.PLANNING
        assert pi.is_planning
        assert pi.is_active
        assert isinstance(session, PlanSession)

    def test_start_while_active_raises(self):
        pi = PlanInterceptor()
        pi.start()
        with pytest.raises(RuntimeError, match="already active"):
            pi.start()

    def test_cancel_returns_to_inactive(self):
        pi = PlanInterceptor()
        pi.start()
        pi.cancel()
        assert pi.state == PlanState.INACTIVE
        assert not pi.is_active

    def test_review_from_planning(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("do thing"))
        pi.review()
        assert pi.state == PlanState.REVIEWING

    def test_review_empty_plan_raises(self):
        pi = PlanInterceptor()
        pi.start()
        with pytest.raises(RuntimeError, match="empty plan"):
            pi.review()

    def test_review_from_wrong_state_raises(self):
        pi = PlanInterceptor()
        with pytest.raises(RuntimeError, match="expected 'planning'"):
            pi.review()

    def test_approve_from_reviewing(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("step"))
        pi.review()
        pi.approve()
        assert pi.state == PlanState.EXECUTING
        assert pi.session.approved_at is not None

    def test_approve_from_wrong_state_raises(self):
        pi = PlanInterceptor()
        pi.start()
        with pytest.raises(RuntimeError, match="expected 'reviewing'"):
            pi.approve()

    def test_complete_from_executing(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("s"))
        pi.review()
        pi.approve()
        pi.complete()
        assert pi.state == PlanState.COMPLETED
        assert not pi.is_active

    def test_complete_from_wrong_state_raises(self):
        pi = PlanInterceptor()
        pi.start()
        with pytest.raises(RuntimeError, match="expected 'executing'"):
            pi.complete()

    def test_full_lifecycle(self):
        """INACTIVE → PLANNING → REVIEWING → EXECUTING → COMPLETED."""
        pi = PlanInterceptor()
        assert pi.state == PlanState.INACTIVE
        pi.start()
        assert pi.state == PlanState.PLANNING
        pi.add_step(PlanStep("step 1", files_affected=["a.py"]))
        pi.add_step(PlanStep("step 2", risk_level="high"))
        pi.review()
        assert pi.state == PlanState.REVIEWING
        pi.approve()
        assert pi.state == PlanState.EXECUTING
        pi.mark_step(0, "done")
        pi.mark_step(1, "done")
        pi.complete()
        assert pi.state == PlanState.COMPLETED


# ─────────────────────────────────────────────────────────────
# PlanInterceptor — step management
# ─────────────────────────────────────────────────────────────


class TestInterceptorSteps:
    def test_add_step_returns_index(self):
        pi = PlanInterceptor()
        pi.start()
        assert pi.add_step(PlanStep("first")) == 0
        assert pi.add_step(PlanStep("second")) == 1

    def test_add_step_wrong_state_raises(self):
        pi = PlanInterceptor()
        with pytest.raises(RuntimeError, match="planning"):
            pi.add_step(PlanStep("nope"))

    def test_record_context(self):
        pi = PlanInterceptor()
        pi.start()
        pi.record_context("read src/main.py")
        pi.record_context("searched for TODOs")
        assert len(pi.session.context_gathered) == 2

    def test_mark_step_valid(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("s"))
        pi.add_step(PlanStep("t"))
        pi.review()
        pi.approve()
        pi.mark_step(0, "in_progress")
        assert pi.session.steps[0].status == "in_progress"
        pi.mark_step(0, "done")
        assert pi.session.steps[0].status == "done"

    def test_mark_step_out_of_range(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("s"))
        with pytest.raises(IndexError):
            pi.mark_step(5, "done")

    def test_mark_step_invalid_status(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("s"))
        with pytest.raises(ValueError, match="Invalid status"):
            pi.mark_step(0, "banana")


# ─────────────────────────────────────────────────────────────
# PlanInterceptor — tool gating
# ─────────────────────────────────────────────────────────────


class TestInterceptorGating:
    def test_inactive_blocks_nothing(self):
        pi = PlanInterceptor()
        assert not pi.should_block("write_file")
        assert not pi.should_block("bash_exec")

    def test_planning_blocks_writes(self):
        pi = PlanInterceptor()
        pi.start()
        assert pi.should_block("write_file")
        assert pi.should_block("bash_exec")
        assert pi.should_block("memory_write")
        assert pi.should_block("wiki_write")

    def test_planning_allows_reads(self):
        pi = PlanInterceptor()
        pi.start()
        assert not pi.should_block("read_file")
        assert not pi.should_block("list_files")
        assert not pi.should_block("search")
        assert not pi.should_block("web_fetch")
        assert not pi.should_block("memory_read")
        assert not pi.should_block("kb_lookup")
        assert not pi.should_block("wiki_search")
        assert not pi.should_block("wiki_read")

    def test_planning_allows_plan_tools(self):
        pi = PlanInterceptor()
        pi.start()
        assert not pi.should_block("plan_add_step")
        assert not pi.should_block("plan_show")
        assert not pi.should_block("plan_approve")

    def test_planning_allows_devops_reads(self):
        pi = PlanInterceptor()
        pi.start()
        for name in (
            "navig_host_show",
            "navig_host_test",
            "navig_host_monitor",
            "navig_app_list",
            "navig_app_show",
            "navig_db_list",
            "navig_docker_ps",
            "navig_file_show",
            "navig_file_list",
            "navig_web_vhosts",
        ):
            assert not pi.should_block(name), f"{name} should be allowed"

    def test_planning_blocks_devops_writes(self):
        pi = PlanInterceptor()
        pi.start()
        for name in ("navig_run", "navig_file_add", "navig_db_query", "navig_docker_restart"):
            assert pi.should_block(name), f"{name} should be blocked"

    def test_executing_blocks_nothing(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("s"))
        pi.review()
        pi.approve()
        assert not pi.should_block("write_file")
        assert not pi.should_block("bash_exec")

    def test_get_block_reason_returns_message(self):
        pi = PlanInterceptor()
        pi.start()
        reason = pi.get_block_reason("write_file")
        assert reason is not None
        assert "plan mode" in reason.lower() or "Blocked" in reason

    def test_get_block_reason_none_for_allowed(self):
        pi = PlanInterceptor()
        pi.start()
        assert pi.get_block_reason("read_file") is None


# ─────────────────────────────────────────────────────────────
# PlanInterceptor — format_plan
# ─────────────────────────────────────────────────────────────


class TestFormatPlan:
    def test_empty_plan(self):
        pi = PlanInterceptor()
        pi.start()
        text = pi.format_plan()
        assert "no steps" in text.lower()

    def test_plan_with_steps(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("Create module", files_affected=["src/mod.py"], risk_level="low"))
        pi.add_step(PlanStep("Write tests", tool_calls=["write_file"], risk_level="medium"))
        text = pi.format_plan()
        assert "Step 1" in text
        assert "Step 2" in text
        assert "Create module" in text
        assert "src/mod.py" in text
        assert "write_file" in text

    def test_context_in_format(self):
        pi = PlanInterceptor()
        pi.start()
        pi.record_context("read README.md")
        pi.add_step(PlanStep("Update docs"))
        text = pi.format_plan()
        assert "README.md" in text


# ─────────────────────────────────────────────────────────────
# Plan tools (unit)
# ─────────────────────────────────────────────────────────────


class TestPlanTools:
    """Test the BaseTool implementations in plan_tools.py."""

    def _run(self, coro):
        """Helper to run coroutines synchronously."""
        return asyncio.run(coro)

    def setup_method(self):
        from navig.agent.tools.plan_tools import set_interceptor

        self.interceptor = PlanInterceptor()
        self.interceptor.start()
        set_interceptor(self.interceptor)

    def test_add_step_success(self):
        from navig.agent.tools.plan_tools import PlanAddStepTool

        tool = PlanAddStepTool()
        result = self._run(tool.run({"description": "Fix the bug", "risk": "medium"}))
        assert result.success
        assert "Step 1" in result.output
        assert self.interceptor.session.step_count == 1
        assert self.interceptor.session.steps[0].risk_level == "medium"

    def test_add_step_with_files_and_tools(self):
        from navig.agent.tools.plan_tools import PlanAddStepTool

        tool = PlanAddStepTool()
        result = self._run(
            tool.run(
                {
                    "description": "Add module",
                    "files": "a.py, b.py",
                    "tools": "write_file, bash_exec",
                }
            )
        )
        assert result.success
        step = self.interceptor.session.steps[0]
        assert step.files_affected == ["a.py", "b.py"]
        assert step.tool_calls == ["write_file", "bash_exec"]

    def test_add_step_empty_description_fails(self):
        from navig.agent.tools.plan_tools import PlanAddStepTool

        tool = PlanAddStepTool()
        result = self._run(tool.run({"description": ""}))
        assert not result.success

    def test_show_plan(self):
        from navig.agent.tools.plan_tools import PlanShowTool

        self.interceptor.add_step(PlanStep("Do thing"))
        tool = PlanShowTool()
        result = self._run(tool.run({}))
        assert result.success
        assert "Do thing" in result.output

    def test_approve_auto_reviews(self):
        from navig.agent.tools.plan_tools import PlanApproveTool

        self.interceptor.add_step(PlanStep("step"))
        # Still in PLANNING — approve should auto review → approve
        tool = PlanApproveTool()
        result = self._run(tool.run({}))
        assert result.success
        assert self.interceptor.state == PlanState.EXECUTING
        assert "approved" in result.output.lower()

    def test_approve_empty_plan_fails(self):
        from navig.agent.tools.plan_tools import PlanApproveTool

        tool = PlanApproveTool()
        result = self._run(tool.run({}))
        # Should fail because plan is empty (can't review)
        assert not result.success


# ─────────────────────────────────────────────────────────────
# Plan tools registration
# ─────────────────────────────────────────────────────────────


class TestPlanToolsRegistration:
    def test_register_plan_tools(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.plan_tools import (
            PlanAddStepTool,
            PlanApproveTool,
            PlanShowTool,
            set_interceptor,
        )

        reg = AgentToolRegistry()
        interceptor = PlanInterceptor()
        set_interceptor(interceptor)

        # Manually register (don't pollute singleton)
        reg.register(PlanAddStepTool(), toolset="plan", check_fn=lambda: interceptor.is_active)
        reg.register(PlanShowTool(), toolset="plan", check_fn=lambda: interceptor.is_active)
        reg.register(PlanApproveTool(), toolset="plan", check_fn=lambda: interceptor.is_active)

        # When inactive, tools should not be available
        assert "plan_add_step" not in reg.available_names()

        # When active, tools should appear
        interceptor.start()
        names = reg.available_names()
        assert "plan_add_step" in names
        assert "plan_show" in names
        assert "plan_approve" in names

    def test_plan_schemas_only_when_active(self):
        from navig.agent.agent_tool_registry import AgentToolRegistry
        from navig.agent.tools.plan_tools import PlanAddStepTool, set_interceptor

        reg = AgentToolRegistry()
        interceptor = PlanInterceptor()
        set_interceptor(interceptor)
        reg.register(PlanAddStepTool(), toolset="plan", check_fn=lambda: interceptor.is_active)

        schemas_inactive = reg.get_openai_schemas(toolsets=["plan"])
        assert len(schemas_inactive) == 0

        interceptor.start()
        schemas_active = reg.get_openai_schemas(toolsets=["plan"])
        assert len(schemas_active) == 1
        assert schemas_active[0]["function"]["name"] == "plan_add_step"


# ─────────────────────────────────────────────────────────────
# Integration: cancel while planning
# ─────────────────────────────────────────────────────────────


class TestCancelFlow:
    def test_cancel_mid_planning_resets(self):
        pi = PlanInterceptor()
        pi.start()
        pi.add_step(PlanStep("a"))
        pi.add_step(PlanStep("b"))
        pi.cancel()
        assert pi.state == PlanState.INACTIVE
        assert pi.session.step_count == 0

    def test_can_start_new_plan_after_cancel(self):
        pi = PlanInterceptor()
        pi.start()
        pi.cancel()
        session = pi.start()
        assert session.state == PlanState.PLANNING
        assert session.step_count == 0
