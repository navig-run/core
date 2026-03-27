"""tests/test_agent_tool_dispatch.py — ToolRouter fallthrough in TaskExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(action: str, params: dict | None = None):
    from navig.agent.conv.executor import ExecutionStep

    return ExecutionStep(
        action=action, description=f"Test {action}", params=params or {}
    )


def _reset():
    from navig.tools.hooks import reset_hook_registry
    from navig.tools.router import reset_globals

    reset_globals()
    reset_hook_registry()


# ---------------------------------------------------------------------------
# ToolRouter fallthrough — unknown action dispatches to ToolRouter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_action_wait_still_works():
    """Hardcoded 'wait' action still resolves without touching ToolRouter."""
    from navig.agent.conv.executor import TaskExecutor

    executor = TaskExecutor()
    step = _make_step("wait", {"seconds": 0})
    result = await executor._execute_step(step)
    assert result == "Waited"


@pytest.mark.asyncio
async def test_known_action_command_still_works():
    """Hardcoded 'command' action still works (runs subprocess directly)."""
    from navig.agent.conv.executor import TaskExecutor

    executor = TaskExecutor()
    step = _make_step("command", {"cmd": "echo hello_cmd"})
    result = await executor._execute_step(step)
    assert "hello_cmd" in str(result)


@pytest.mark.asyncio
async def test_fallthrough_to_tool_router_success():
    """An action unknown to executor but known to ToolRouter succeeds."""
    _reset()
    from navig.agent.conv.executor import TaskExecutor
    from navig.tools.router import get_tool_router
    from navig.tools.schemas import ToolCallAction
    from navig.tools.schemas import ToolResult as RouterToolResult
    from navig.tools.schemas import ToolResultStatus

    mock_result = RouterToolResult(
        tool="system_info",
        status=ToolResultStatus.SUCCESS,
        output={"platform": "test-os"},
    )

    with patch.object(
        get_tool_router().__class__, "async_execute", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = mock_result
        executor = TaskExecutor()
        step = _make_step("system_info")
        result = await executor._execute_step(step)

    assert result == {"platform": "test-os"}


@pytest.mark.asyncio
async def test_fallthrough_not_found_raises_value_error():
    """NOT_FOUND from ToolRouter re-raises as ValueError (original contract)."""
    _reset()
    from navig.agent.conv.executor import TaskExecutor
    from navig.tools.router import get_tool_router
    from navig.tools.schemas import ToolResult as RouterToolResult
    from navig.tools.schemas import ToolResultStatus

    mock_result = RouterToolResult(
        tool="truly_unknown",
        status=ToolResultStatus.NOT_FOUND,
        error="Unknown tool: truly_unknown",
    )

    with patch.object(
        get_tool_router().__class__, "async_execute", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = mock_result
        executor = TaskExecutor()
        step = _make_step("truly_unknown")
        with pytest.raises(ValueError, match="Unknown action"):
            await executor._execute_step(step)


@pytest.mark.asyncio
async def test_fallthrough_error_raises_runtime_error():
    """ERROR status from ToolRouter raises RuntimeError with details."""
    _reset()
    from navig.agent.conv.executor import TaskExecutor
    from navig.tools.router import get_tool_router
    from navig.tools.schemas import ToolResult as RouterToolResult
    from navig.tools.schemas import ToolResultStatus

    mock_result = RouterToolResult(
        tool="bash_exec",
        status=ToolResultStatus.ERROR,
        error="Something went wrong",
    )

    with patch.object(
        get_tool_router().__class__, "async_execute", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = mock_result
        executor = TaskExecutor()
        step = _make_step("bash_exec", {"command": "bad"})
        with pytest.raises(RuntimeError, match="Something went wrong"):
            await executor._execute_step(step)


@pytest.mark.asyncio
async def test_fallthrough_denied_raises_runtime_error():
    """DENIED status from ToolRouter raises RuntimeError."""
    _reset()
    from navig.agent.conv.executor import TaskExecutor
    from navig.tools.router import get_tool_router
    from navig.tools.schemas import ToolResult as RouterToolResult
    from navig.tools.schemas import ToolResultStatus

    mock_result = RouterToolResult(
        tool="bash_exec",
        status=ToolResultStatus.DENIED,
        error="Tool blocked by policy",
    )

    with patch.object(
        get_tool_router().__class__, "async_execute", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = mock_result
        executor = TaskExecutor()
        step = _make_step("bash_exec", {"command": "rm -rf /"})
        with pytest.raises(RuntimeError, match="denied"):
            await executor._execute_step(step)


# ---------------------------------------------------------------------------
# execute_multi_step_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_multi_step_action_success():
    """execute_multi_step_action chains multiple tool calls."""
    _reset()
    from navig.agent.conv.executor import TaskExecutor
    from navig.tools.router import get_tool_router
    from navig.tools.schemas import MultiStepAction, ToolCallAction
    from navig.tools.schemas import ToolResult as RouterToolResult
    from navig.tools.schemas import ToolResultStatus

    mock_result = RouterToolResult(
        tool="system_info",
        status=ToolResultStatus.SUCCESS,
        output="sys_ok",
    )

    with patch.object(
        get_tool_router().__class__, "async_execute", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = mock_result
        executor = TaskExecutor()
        multi = MultiStepAction(
            steps=[
                ToolCallAction(tool="system_info"),
                ToolCallAction(tool="system_info"),
            ]
        )
        result = await executor.execute_multi_step_action(multi)

    assert "[1] system_info:" in result
    assert "[2] system_info:" in result
    assert mock_exec.call_count == 2


@pytest.mark.asyncio
async def test_execute_multi_step_action_empty():
    """An empty MultiStepAction returns the empty sentinel string."""
    _reset()
    from navig.agent.conv.executor import TaskExecutor
    from navig.tools.schemas import MultiStepAction

    executor = TaskExecutor()
    result = await executor.execute_multi_step_action(MultiStepAction(steps=[]))
    assert result == "(no steps executed)"


@pytest.mark.asyncio
async def test_execute_multi_step_action_stops_on_failure():
    """execute_multi_step_action halts when a step fails."""
    _reset()
    from navig.agent.conv.executor import TaskExecutor
    from navig.tools.router import get_tool_router
    from navig.tools.schemas import MultiStepAction, ToolCallAction
    from navig.tools.schemas import ToolResult as RouterToolResult
    from navig.tools.schemas import ToolResultStatus

    ok_result = RouterToolResult(
        tool="t1", status=ToolResultStatus.SUCCESS, output="ok"
    )
    fail_result = RouterToolResult(
        tool="t2", status=ToolResultStatus.ERROR, error="Boom"
    )

    call_log: list[str] = []

    async def side_effect(action: ToolCallAction):
        call_log.append(action.tool)
        if action.tool == "t2":
            return fail_result
        return ok_result

    with patch.object(
        get_tool_router().__class__, "async_execute", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.side_effect = side_effect
        executor = TaskExecutor()
        multi = MultiStepAction(
            steps=[
                ToolCallAction(tool="t1"),
                ToolCallAction(tool="t2"),
                ToolCallAction(tool="t3"),  # must NOT be called
            ]
        )
        with pytest.raises(RuntimeError, match="Boom"):
            await executor.execute_multi_step_action(multi)

    assert "t3" not in call_log  # halted before 3rd step


# ---------------------------------------------------------------------------
# Bridge — adapt_base_tool / bridge_all
# ---------------------------------------------------------------------------


def test_adapt_base_tool_produces_valid_meta():
    from navig.tools.bridge import adapt_base_tool
    from navig.tools.registry import BaseTool, ToolResult

    class MyTool(BaseTool):
        name = "my_tool"
        description = "A test tool"

        async def run(self, args, on_status=None):
            return ToolResult(name=self.name, success=True, output={"ok": True})

    meta, handler = adapt_base_tool(MyTool())
    assert meta.name == "my_tool"
    assert meta.description == "A test tool"
    assert "bridged" in meta.tags
    assert callable(handler)


@pytest.mark.asyncio
async def test_bridged_handler_returns_output_on_success():
    from navig.tools.bridge import adapt_base_tool
    from navig.tools.registry import BaseTool, ToolResult

    class EchoTool(BaseTool):
        name = "echo_tool"
        description = "Echo args"

        async def run(self, args, on_status=None):
            return ToolResult(name=self.name, success=True, output=args)

    _, handler = adapt_base_tool(EchoTool())
    result = await handler(foo="bar")
    assert result == {"foo": "bar"}


@pytest.mark.asyncio
async def test_bridged_handler_raises_on_failure():
    from navig.tools.bridge import adapt_base_tool
    from navig.tools.registry import BaseTool, ToolResult

    class FailTool(BaseTool):
        name = "fail_tool"
        description = "Always fails"

        async def run(self, args, on_status=None):
            return ToolResult(name=self.name, success=False, error="deliberate failure")

    _, handler = adapt_base_tool(FailTool())
    with pytest.raises(RuntimeError, match="deliberate failure"):
        await handler()


def test_bridge_all_registers_tools():
    from navig.tools.bridge import bridge_all
    from navig.tools.registry import BaseTool
    from navig.tools.registry import ToolRegistry as BaseRegistry
    from navig.tools.registry import ToolResult
    from navig.tools.router import ToolRegistry as RouterRegistry

    class ToolA(BaseTool):
        name = "tool_a"
        description = "A"

        async def run(self, args, on_status=None):
            return ToolResult(name=self.name, success=True, output="a")

    class ToolB(BaseTool):
        name = "tool_b"
        description = "B"

        async def run(self, args, on_status=None):
            return ToolResult(name=self.name, success=True, output="b")

    base_reg = BaseRegistry()
    base_reg.register(ToolA())
    base_reg.register(ToolB())

    router_reg = RouterRegistry()
    count = bridge_all(base_reg, router_reg)
    assert count == 2
    assert router_reg.get_tool("tool_a") is not None
    assert router_reg.get_tool("tool_b") is not None


def test_bridge_all_skips_duplicates():
    from navig.tools.bridge import bridge_all
    from navig.tools.registry import BaseTool
    from navig.tools.registry import ToolRegistry as BaseRegistry
    from navig.tools.registry import ToolResult
    from navig.tools.router import ToolRegistry as RouterRegistry

    class DupTool(BaseTool):
        name = "dup_tool"
        description = "Dup"

        async def run(self, args, on_status=None):
            return ToolResult(name=self.name, success=True, output="dup")

    base_reg = BaseRegistry()
    base_reg.register(DupTool())

    router_reg = RouterRegistry()
    count1 = bridge_all(base_reg, router_reg)
    count2 = bridge_all(base_reg, router_reg, overwrite=False)  # should skip
    assert count1 == 1
    assert count2 == 0


# ---------------------------------------------------------------------------
# Skill eligibility
# ---------------------------------------------------------------------------


def test_is_eligible_safe_default():
    from pathlib import Path

    from navig.skills.eligibility import SkillEligibilityContext, is_eligible
    from navig.skills.loader import Skill

    skill = Skill(
        id="s1",
        name="s1",
        version="1",
        category="general",
        tags=[],
        platforms=["all"],
        tools=[],
        safety="safe",
        body_markdown="",
        examples=[],
        source_path=Path("."),
    )
    ctx = SkillEligibilityContext.default()
    assert is_eligible(skill, ctx) is True


def test_is_eligible_blocks_destructive_in_default():
    from pathlib import Path

    from navig.skills.eligibility import SkillEligibilityContext, is_eligible
    from navig.skills.loader import Skill

    skill = Skill(
        id="s1",
        name="s1",
        version="1",
        category="general",
        tags=[],
        platforms=["all"],
        tools=[],
        safety="destructive",
        body_markdown="",
        examples=[],
        source_path=Path("."),
    )
    ctx = SkillEligibilityContext.default()
    assert is_eligible(skill, ctx) is False


def test_is_eligible_platform_filter():
    from pathlib import Path

    from navig.skills.eligibility import SkillEligibilityContext, is_eligible
    from navig.skills.loader import Skill

    skill = Skill(
        id="s1",
        name="s1",
        version="1",
        category="general",
        tags=[],
        platforms=["linux"],
        tools=[],
        safety="safe",
        body_markdown="",
        examples=[],
        source_path=Path("."),
    )
    ctx_linux = SkillEligibilityContext(platform="linux", safety_max="elevated")
    ctx_win = SkillEligibilityContext(platform="windows", safety_max="elevated")
    assert is_eligible(skill, ctx_linux) is True
    assert is_eligible(skill, ctx_win) is False


def test_is_eligible_user_invocable_gate():
    from pathlib import Path

    from navig.skills.eligibility import SkillEligibilityContext, is_eligible
    from navig.skills.loader import Skill

    skill = Skill(
        id="s1",
        name="s1",
        version="1",
        category="general",
        tags=[],
        platforms=["all"],
        tools=[],
        safety="safe",
        body_markdown="",
        examples=[],
        source_path=Path("."),
        user_invocable=False,
    )
    ctx_open = SkillEligibilityContext(
        platform="all", safety_max="safe", user_invocable_only=False
    )
    ctx_only = SkillEligibilityContext(
        platform="all", safety_max="safe", user_invocable_only=True
    )
    assert is_eligible(skill, ctx_open) is True
    assert is_eligible(skill, ctx_only) is False


def test_is_eligible_required_tags():
    from pathlib import Path

    from navig.skills.eligibility import SkillEligibilityContext, is_eligible
    from navig.skills.loader import Skill

    skill = Skill(
        id="s1",
        name="s1",
        version="1",
        category="general",
        tags=["devops", "docker"],
        platforms=["all"],
        tools=[],
        safety="safe",
        body_markdown="",
        examples=[],
        source_path=Path("."),
    )
    ctx_ok = SkillEligibilityContext(
        platform="all", safety_max="safe", required_tags=["devops"]
    )
    ctx_miss = SkillEligibilityContext(
        platform="all", safety_max="safe", required_tags=["kubernetes"]
    )
    assert is_eligible(skill, ctx_ok) is True
    assert is_eligible(skill, ctx_miss) is False


def test_is_eligible_excluded_tags():
    from pathlib import Path

    from navig.skills.eligibility import SkillEligibilityContext, is_eligible
    from navig.skills.loader import Skill

    skill = Skill(
        id="s1",
        name="s1",
        version="1",
        category="general",
        tags=["dangerous", "system"],
        platforms=["all"],
        tools=[],
        safety="safe",
        body_markdown="",
        examples=[],
        source_path=Path("."),
    )
    ctx = SkillEligibilityContext(
        platform="all", safety_max="safe", excluded_tags=["dangerous"]
    )
    assert is_eligible(skill, ctx) is False


def test_filter_skills_basic():
    from pathlib import Path

    from navig.skills.eligibility import SkillEligibilityContext, filter_skills
    from navig.skills.loader import Skill

    def make_skill(id_, safety):
        return Skill(
            id=id_,
            name=id_,
            version="1",
            category="general",
            tags=[],
            platforms=["all"],
            tools=[],
            safety=safety,
            body_markdown="",
            examples=[],
            source_path=Path("."),
        )

    all_skills = {
        "safe_a": make_skill("safe_a", "safe"),
        "elevated_b": make_skill("elevated_b", "elevated"),
        "destr_c": make_skill("destr_c", "destructive"),
    }
    ctx = SkillEligibilityContext.default()
    result = filter_skills(list(all_skills), all_skills, ctx)
    assert "safe_a" in result
    assert "elevated_b" in result
    assert "destr_c" not in result


def test_filter_skills_drops_unknown_ids():
    from navig.skills.eligibility import SkillEligibilityContext, filter_skills

    ctx = SkillEligibilityContext.default()
    result = filter_skills(["nonexistent_id_xyz"], {}, ctx)
    assert result == []
