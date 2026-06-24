"""
Batch 77: navig/tools/interfaces.py, navig/tools/schemas.py,
          navig/installer/profiles.py
"""
from __future__ import annotations

import asyncio
import pytest

# ---------------------------------------------------------------------------
# tools/interfaces.py
# ---------------------------------------------------------------------------
from navig.tools.interfaces import (
    EventPhase,
    StreamStatus,
    StreamChunk,
    StreamFinal,
    StreamError,
    ExecutionContext,
    ExecutionRequest,
    EndState,
    ExecutionResult,
    ToolSpec,
    SkillSpec,
)


class TestEventPhase:
    def test_values(self):
        assert EventPhase.STATUS == "status"
        assert EventPhase.CHUNK == "chunk"
        assert EventPhase.FINAL == "final"
        assert EventPhase.ERROR == "error"


class TestStreamEvents:
    def test_stream_status_phase(self):
        s = StreamStatus(step="preparing", detail="loading", progress=10)
        assert s.phase == EventPhase.STATUS
        assert s.step == "preparing"
        assert s.progress == 10

    def test_stream_status_defaults(self):
        s = StreamStatus(step="x")
        assert s.detail == ""
        assert s.progress == 0

    def test_stream_chunk_phase(self):
        c = StreamChunk(chunk="hello")
        assert c.phase == EventPhase.CHUNK
        assert c.chunk == "hello"

    def test_stream_final_phase(self):
        f = StreamFinal(output={"result": 42})
        assert f.phase == EventPhase.FINAL
        assert f.output == {"result": 42}

    def test_stream_error_phase(self):
        e = StreamError(message="boom", code="timeout")
        assert e.phase == EventPhase.ERROR
        assert e.message == "boom"
        assert e.code == "timeout"

    def test_stream_error_default_code(self):
        e = StreamError(message="fail")
        assert e.code == "error"

    def test_frozen_status(self):
        s = StreamStatus(step="s")
        with pytest.raises((AttributeError, TypeError)):
            s.step = "other"  # type: ignore[misc]


class TestExecutionContext:
    def test_defaults(self):
        ctx = ExecutionContext()
        assert ctx.session_id == ""
        assert ctx.agent_id == ""
        assert ctx.env == {}
        assert ctx.owner_only is False

    def test_custom(self):
        ctx = ExecutionContext(session_id="s1", agent_id="a1", owner_only=True)
        assert ctx.session_id == "s1"
        assert ctx.owner_only is True


class TestExecutionRequest:
    def test_basic(self):
        req = ExecutionRequest(tool_name="search", args={"q": "test"})
        assert req.tool_name == "search"
        assert req.args == {"q": "test"}
        assert req.timeout_s == 120.0
        assert req.lane == "main"

    def test_is_cancelled_no_token(self):
        req = ExecutionRequest(tool_name="x", args={})
        assert req.is_cancelled is False

    def test_is_cancelled_with_set_event(self):
        ev = asyncio.Event()
        ev.set()
        req = ExecutionRequest(tool_name="x", args={}, cancellation_token=ev)
        assert req.is_cancelled is True

    def test_is_not_cancelled_with_clear_event(self):
        ev = asyncio.Event()
        req = ExecutionRequest(tool_name="x", args={}, cancellation_token=ev)
        assert req.is_cancelled is False


class TestExecutionResult:
    def test_success(self):
        r = ExecutionResult(state=EndState.SUCCESS, output="ok")
        assert r.state == EndState.SUCCESS
        assert r.output == "ok"
        assert r.elapsed_ms == 0.0

    def test_error(self):
        r = ExecutionResult(state=EndState.ERROR, error="oops")
        assert r.error == "oops"
        assert r.state == EndState.ERROR


class TestToolSpec:
    def test_get_meta(self):
        spec = ToolSpec(id="web", name="Web Search", description="searches the web", domain="net")
        meta = spec.get_meta()
        assert meta["id"] == "web"
        assert meta["name"] == "Web Search"
        assert meta["domain"] == "net"
        assert "parameters" in meta

    def test_name_defaults_to_id(self):
        spec = ToolSpec(id="my_tool")
        meta = spec.get_meta()
        assert meta["name"] == "my_tool"

    def test_validate_args_returns_true(self):
        spec = ToolSpec(id="t")
        assert spec.validate_args({"a": 1}) is True


class TestSkillSpec:
    def test_basic(self):
        skill = SkillSpec(id="s1", name="Skill One", description="does stuff")
        assert skill.id == "s1"
        assert skill.version == "1.0.0"
        assert skill.tools == []


# ---------------------------------------------------------------------------
# tools/schemas.py
# ---------------------------------------------------------------------------
from navig.tools.schemas import (
    ActionType,
    ToolResultStatus,
    ToolCallAction,
    RespondAction,
    MultiStepAction,
    ToolResult,
    parse_llm_action,
    format_tool_result_for_llm,
)


class TestActionType:
    def test_values(self):
        assert ActionType.TOOL_CALL == "tool_call"
        assert ActionType.RESPOND == "respond"
        assert ActionType.MULTI_STEP == "multi_step"


class TestToolResult:
    def test_success_property(self):
        r = ToolResult(tool="web", status=ToolResultStatus.SUCCESS, output="ok")
        assert r.success is True

    def test_failure_property(self):
        r = ToolResult(tool="web", status=ToolResultStatus.ERROR, error="fail")
        assert r.success is False

    def test_to_dict(self):
        r = ToolResult(tool="t", output=42)
        d = r.to_dict()
        assert d["tool"] == "t"
        assert d["output"] == 42
        assert d["status"] == "success"


class TestParseLlmAction:
    def test_empty_text_returns_respond(self):
        result = parse_llm_action("")
        assert isinstance(result, RespondAction)
        assert result.message == ""

    def test_plain_text_returns_respond(self):
        result = parse_llm_action("Hello there")
        assert isinstance(result, RespondAction)
        assert result.message == "Hello there"

    def test_tool_call_json(self):
        text = '{"action": "tool_call", "tool": "web_search", "parameters": {"query": "python"}}'
        result = parse_llm_action(text)
        assert isinstance(result, ToolCallAction)
        assert result.tool == "web_search"
        assert result.parameters == {"query": "python"}
        assert result.action_type == ActionType.TOOL_CALL

    def test_respond_json(self):
        text = '{"action": "respond", "message": "Here is your answer"}'
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)
        assert result.message == "Here is your answer"

    def test_multi_step_json(self):
        text = '{"action": "multi_step", "steps": [{"tool": "search", "parameters": {}}, {"tool": "calc", "parameters": {}}]}'
        result = parse_llm_action(text)
        assert isinstance(result, MultiStepAction)
        assert len(result.steps) == 2
        assert result.steps[0].tool == "search"

    def test_fenced_code_block(self):
        text = '```json\n{"action": "tool_call", "tool": "image_gen", "parameters": {}}\n```'
        result = parse_llm_action(text)
        assert isinstance(result, ToolCallAction)
        assert result.tool == "image_gen"

    def test_tool_call_missing_tool_falls_back(self):
        text = '{"action": "tool_call"}'
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)

    def test_multi_step_no_steps_falls_back(self):
        text = '{"action": "multi_step", "steps": []}'
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)

    def test_unknown_action_with_tool_key(self):
        text = '{"action": "unknown_action", "tool": "helper"}'
        result = parse_llm_action(text)
        assert isinstance(result, ToolCallAction)
        assert result.tool == "helper"

    def test_whitespace_only_returns_respond_empty(self):
        result = parse_llm_action("   \n  ")
        assert isinstance(result, RespondAction)


class TestFormatToolResultForLlm:
    def test_success_format(self):
        r = ToolResult(tool="search", output={"results": ["a", "b"]})
        text = format_tool_result_for_llm(r)
        assert "search" in text
        assert "results" in text

    def test_error_format(self):
        r = ToolResult(tool="calc", status=ToolResultStatus.ERROR, error="divide by zero")
        text = format_tool_result_for_llm(r)
        assert "calc" in text
        assert "divide by zero" in text
        assert "error" in text


# ---------------------------------------------------------------------------
# installer/profiles.py
# ---------------------------------------------------------------------------
from navig.installer.profiles import PROFILE_MODULES, DEFAULT_PROFILE, VALID_PROFILES


class TestInstallerProfiles:
    def test_default_profile(self):
        assert DEFAULT_PROFILE == "operator"

    def test_valid_profiles_list(self):
        assert set(VALID_PROFILES) == set(PROFILE_MODULES.keys())

    def test_all_expected_profiles_present(self):
        for p in ("node", "operator", "architect", "system_standard", "system_deep"):
            assert p in PROFILE_MODULES

    def test_node_is_minimal(self):
        assert "config_paths" in PROFILE_MODULES["node"]
        assert "core_cli" in PROFILE_MODULES["node"]

    def test_operator_extends_node(self):
        node_mods = set(PROFILE_MODULES["node"])
        operator_mods = set(PROFILE_MODULES["operator"])
        assert node_mods.issubset(operator_mods)

    def test_architect_extends_operator(self):
        operator_mods = set(PROFILE_MODULES["operator"])
        architect_mods = set(PROFILE_MODULES["architect"])
        assert operator_mods.issubset(architect_mods)

    def test_system_deep_has_tray_and_persona(self):
        assert "tray" in PROFILE_MODULES["system_deep"]
        assert "persona_assets" in PROFILE_MODULES["system_deep"]

    def test_profiles_are_ordered_lists(self):
        for name, mods in PROFILE_MODULES.items():
            assert isinstance(mods, list), f"{name} should be a list"

    def test_node_does_not_have_telegram(self):
        assert "telegram" not in PROFILE_MODULES["node"]
