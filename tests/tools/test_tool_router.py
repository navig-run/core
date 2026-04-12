"""
Tests for navig.tools — Tool Router, Registry, Schemas, and Packs.

Test structure:
  TestSchemaParser          — parse_llm_action() with various inputs
  TestToolRegistry          — ToolRegistry registration, lookup, aliasing
  TestToolRouter            — ToolRouter execution, safety, policy
  TestToolPacks             — Builtin pack registration (web, image, code, system, data)
  TestToolResultFormatting  — format_tool_result_for_llm()
  TestIntegration           — End-to-end: parse -> route -> execute -> format
"""

from __future__ import annotations

import json
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict
from unittest.mock import patch

import pytest

from navig.tools.router import (
    SafetyLevel,
    ToolDomain,
    ToolMeta,
    ToolRegistry,
    ToolRouter,
    ToolStatus,
    reset_globals,
)
from navig.tools.schemas import (
    ActionType,
    MultiStepAction,
    RespondAction,
    ToolCallAction,
    ToolResult,
    ToolResultStatus,
    format_tool_result_for_llm,
    parse_llm_action,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_registry() -> ToolRegistry:
    """Create a clean registry without loading builtin packs."""
    reg = ToolRegistry()
    reg._initialized = True  # Skip auto-init (no pack loading)
    return reg


def _echo_handler(**kwargs) -> Dict[str, Any]:
    """Simple handler that echoes its parameters."""
    return {"echo": kwargs}


def _failing_handler(**kwargs):
    """Handler that always raises."""
    raise RuntimeError("intentional failure")


def _register_echo(
    registry: ToolRegistry,
    name: str = "echo_tool",
    domain: ToolDomain = ToolDomain.GENERAL,
    safety: SafetyLevel = SafetyLevel.SAFE,
) -> ToolMeta:
    """Register a simple echo tool and return its meta."""
    meta = ToolMeta(
        name=name,
        domain=domain,
        description="Test echo tool",
        safety=safety,
        status=ToolStatus.AVAILABLE,
    )
    registry.register(meta, handler=_echo_handler)
    return meta


# ===========================================================================
# Test 1: Schema Parser
# ===========================================================================


class TestSchemaParser:
    """parse_llm_action() with various input patterns."""

    def test_tool_call_json(self):
        """JSON with action=tool_call parses to ToolCallAction."""
        text = '{"action": "tool_call", "tool": "web_search", "parameters": {"query": "python"}}'
        action = parse_llm_action(text)
        assert isinstance(action, ToolCallAction)
        assert action.tool == "web_search"
        assert action.parameters == {"query": "python"}
        assert action.action_type == ActionType.TOOL_CALL

    def test_respond_json(self):
        """JSON with action=respond parses to RespondAction."""
        text = '{"action": "respond", "message": "Hello!"}'
        action = parse_llm_action(text)
        assert isinstance(action, RespondAction)
        assert action.message == "Hello!"
        assert action.action_type == ActionType.RESPOND

    def test_multi_step_json(self):
        """JSON with action=multi_step parses to MultiStepAction."""
        text = json.dumps(
            {
                "action": "multi_step",
                "steps": [
                    {"tool": "web_search", "parameters": {"query": "test"}},
                    {"tool": "web_fetch", "parameters": {"url": "http://example.com"}},
                ],
                "reason": "research chain",
            }
        )
        action = parse_llm_action(text)
        assert isinstance(action, MultiStepAction)
        assert len(action.steps) == 2
        assert action.steps[0].tool == "web_search"
        assert action.steps[1].tool == "web_fetch"
        assert action.reason == "research chain"

    def test_plain_text_becomes_respond(self):
        """Plain text without JSON becomes RespondAction."""
        text = "I don't need any tools for this answer."
        action = parse_llm_action(text)
        assert isinstance(action, RespondAction)
        assert action.message == text

    def test_empty_text(self):
        """Empty text becomes empty RespondAction."""
        action = parse_llm_action("")
        assert isinstance(action, RespondAction)
        assert action.message == ""

    def test_json_code_block(self):
        """JSON inside a ```json code block is extracted."""
        text = 'Here is what I will do:\n```json\n{"action": "tool_call", "tool": "docs_search", "parameters": {"query": "deploy"}}\n```'
        action = parse_llm_action(text)
        assert isinstance(action, ToolCallAction)
        assert action.tool == "docs_search"

    def test_bare_tool_json_no_action_field(self):
        """JSON with 'tool' but no 'action' field still parses as tool_call."""
        text = '{"tool": "image_generate", "parameters": {"prompt": "sunset"}}'
        action = parse_llm_action(text)
        assert isinstance(action, ToolCallAction)
        assert action.tool == "image_generate"

    def test_trailing_comma_tolerance(self):
        """JSON with trailing comma is handled gracefully."""
        text = '{"action": "tool_call", "tool": "web_search", "parameters": {"query": "test",},}'
        action = parse_llm_action(text)
        assert isinstance(action, ToolCallAction)
        assert action.tool == "web_search"


# ===========================================================================
# Test 2: Tool Registry
# ===========================================================================


class TestToolRegistry:
    """ToolRegistry registration, lookup, alias normalization."""

    def test_register_and_get(self):
        """Register a tool and retrieve it by name."""
        reg = _fresh_registry()
        meta = _register_echo(reg)
        retrieved = reg.get_tool("echo_tool")
        assert retrieved is not None
        assert retrieved.name == "echo_tool"

    def test_alias_lookup(self):
        """Aliases resolve to canonical tool names."""
        reg = _fresh_registry()
        reg.register(
            ToolMeta(
                name="web_search",
                domain=ToolDomain.WEB,
                description="Search",
                status=ToolStatus.AVAILABLE,
            ),
            handler=_echo_handler,
        )
        # "search" and "google" are aliases for "web_search"
        assert reg.normalize_tool_name("search") == "web_search"
        assert reg.normalize_tool_name("google") == "web_search"

    def test_unknown_tool_returns_none(self):
        """Unknown tool name returns None."""
        reg = _fresh_registry()
        assert reg.get_tool("nonexistent_tool") is None
        assert reg.normalize_tool_name("nonexistent_tool") is None

    def test_list_tools_filters_by_domain(self):
        """list_tools() with domain filter returns only matching tools."""
        reg = _fresh_registry()
        _register_echo(reg, "tool_a", ToolDomain.WEB)
        _register_echo(reg, "tool_b", ToolDomain.CODE)
        _register_echo(reg, "tool_c", ToolDomain.WEB)
        web_tools = reg.list_tools(domain=ToolDomain.WEB)
        assert len(web_tools) == 2
        assert all(t.domain == ToolDomain.WEB for t in web_tools)

    def test_list_tools_available_only(self):
        """list_tools(available_only=True) excludes unavailable tools."""
        reg = _fresh_registry()
        _register_echo(reg, "active")
        reg.register(
            ToolMeta(
                name="disabled",
                domain=ToolDomain.GENERAL,
                status=ToolStatus.DISABLED,
            )
        )
        available = reg.list_tools(available_only=True)
        assert len(available) == 1
        assert available[0].name == "active"

    def test_handler_lazy_load(self):
        """Handler is lazy-loaded from module_path + handler_name."""
        reg = _fresh_registry()
        reg.register(
            ToolMeta(
                name="system_info",
                domain=ToolDomain.SYSTEM,
                module_path="navig.tools.domains.system_pack",
                handler_name="_system_info",
                status=ToolStatus.AVAILABLE,
            )
        )
        handler = reg.get_handler("system_info")
        assert handler is not None
        result = handler()
        assert "platform" in result

    def test_get_tools_for_llm_prompt(self):
        """get_tools_for_llm_prompt() returns serializable dicts."""
        reg = _fresh_registry()
        _register_echo(reg, "t1", ToolDomain.WEB)
        prompt_tools = reg.get_tools_for_llm_prompt()
        assert isinstance(prompt_tools, list)
        assert len(prompt_tools) == 1
        assert prompt_tools[0]["name"] == "t1"
        # Must be JSON-serializable
        json.dumps(prompt_tools)

    def test_status_summary(self):
        """get_status_summary() returns correct counts."""
        reg = _fresh_registry()
        _register_echo(reg, "a")
        _register_echo(reg, "b")
        reg.register(ToolMeta(name="c", domain=ToolDomain.GENERAL, status=ToolStatus.DISABLED))
        summary = reg.get_status_summary()
        assert summary["total"] == 3
        assert summary["available"] == 2


# ===========================================================================
# Test 3: Tool Router Execution
# ===========================================================================


class TestToolRouter:
    """ToolRouter execute() with various scenarios."""

    def test_successful_execution(self):
        """Executing a valid tool returns SUCCESS result."""
        reg = _fresh_registry()
        _register_echo(reg)
        router = ToolRouter(registry=reg)
        action = ToolCallAction(tool="echo_tool", parameters={"msg": "hi"})
        result = router.execute(action)
        assert result.success
        assert result.status == ToolResultStatus.SUCCESS
        assert result.output == {"echo": {"msg": "hi"}}
        assert result.latency_ms >= 0

    def test_unknown_tool_returns_not_found(self):
        """Executing an unknown tool returns NOT_FOUND."""
        reg = _fresh_registry()
        router = ToolRouter(registry=reg)
        action = ToolCallAction(tool="ghost_tool")
        result = router.execute(action)
        assert result.status == ToolResultStatus.NOT_FOUND
        assert "Unknown tool" in result.error

    def test_blocked_tool_returns_denied(self):
        """Tools in blocked_tools policy are denied."""
        reg = _fresh_registry()
        _register_echo(reg)
        router = ToolRouter(
            registry=reg,
            safety_policy={"blocked_tools": ["echo_tool"]},
        )
        action = ToolCallAction(tool="echo_tool")
        result = router.execute(action)
        assert result.status == ToolResultStatus.DENIED
        assert "blocked" in result.error

    def test_confirmation_required_returns_needs_confirmation(self):
        """Tools in require_confirmation policy return NEEDS_CONFIRMATION."""
        reg = _fresh_registry()
        _register_echo(reg)
        router = ToolRouter(
            registry=reg,
            safety_policy={"require_confirmation": ["echo_tool"]},
        )
        action = ToolCallAction(tool="echo_tool")
        result = router.execute(action)
        assert result.status == ToolResultStatus.NEEDS_CONFIRMATION
        assert "confirmation" in result.error

    def test_handler_exception_returns_error(self):
        """Handler raising an exception returns ERROR result."""
        reg = _fresh_registry()
        meta = ToolMeta(
            name="bad_tool",
            domain=ToolDomain.GENERAL,
            status=ToolStatus.AVAILABLE,
        )
        reg.register(meta, handler=_failing_handler)
        router = ToolRouter(registry=reg)
        action = ToolCallAction(tool="bad_tool")
        result = router.execute(action)
        assert result.status == ToolResultStatus.ERROR
        assert "intentional failure" in result.error

    def test_execute_multi_respects_limit(self):
        """execute_multi() stops after max_calls_per_turn."""
        reg = _fresh_registry()
        _register_echo(reg)
        router = ToolRouter(registry=reg, safety_policy={"max_calls_per_turn": 2})
        actions = [ToolCallAction(tool="echo_tool", parameters={"i": i}) for i in range(5)]
        results = router.execute_multi(actions)
        assert len(results) == 3  # 2 executed + 1 denied
        assert results[0].success
        assert results[1].success
        assert results[2].status == ToolResultStatus.DENIED

    @patch("navig.safety_guard.classify_action_risk", return_value="destructive")
    def test_dangerous_tool_safety_guard(self, mock_risk):
        """Dangerous tool with destructive params is denied by safety guard."""
        reg = _fresh_registry()
        meta = ToolMeta(
            name="danger_tool",
            domain=ToolDomain.CODE,
            safety=SafetyLevel.DANGEROUS,
            status=ToolStatus.AVAILABLE,
        )
        reg.register(meta, handler=_echo_handler)
        router = ToolRouter(registry=reg)
        action = ToolCallAction(tool="danger_tool", parameters={"cmd": "rm -rf /"})
        result = router.execute(action)
        assert result.status == ToolResultStatus.DENIED
        assert "Destructive" in result.error


# ===========================================================================
# Test 4: Tool Packs Registration
# ===========================================================================


class TestToolPacks:
    """Builtin packs register the expected tools."""

    def test_web_pack_registers_three_tools(self):
        """Web pack registers web_search, web_fetch, docs_search."""
        reg = _fresh_registry()
        from navig.tools.domains.web_pack import register_tools

        register_tools(reg)
        assert reg.get_tool("web_search") is not None
        assert reg.get_tool("web_fetch") is not None
        assert reg.get_tool("docs_search") is not None

    def test_image_pack_registers_image_generate(self):
        """Image pack registers image_generate."""
        reg = _fresh_registry()
        from navig.tools.domains.image_pack import register_tools

        register_tools(reg)
        tool = reg.get_tool("image_generate")
        assert tool is not None
        assert tool.domain == ToolDomain.IMAGE

    def test_code_pack_registers_code_sandbox(self):
        """Code pack registers code_sandbox as DANGEROUS."""
        reg = _fresh_registry()
        from navig.tools.domains.code_pack import register_tools

        register_tools(reg)
        tool = reg.get_tool("code_sandbox")
        assert tool is not None
        assert tool.safety == SafetyLevel.DANGEROUS

    def test_system_pack_registers_system_tools(self):
        """System pack registers system_info and file_read."""
        reg = _fresh_registry()
        from navig.tools.domains.system_pack import register_tools

        register_tools(reg)
        assert reg.get_tool("system_info") is not None
        assert reg.get_tool("file_read") is not None

    def test_data_pack_registers_json_parse(self):
        """Data pack registers json_parse."""
        reg = _fresh_registry()
        from navig.tools.domains.data_pack import register_tools

        register_tools(reg)
        assert reg.get_tool("json_parse") is not None

    def test_system_info_handler_returns_platform(self):
        """system_info handler returns valid platform data."""
        reg = _fresh_registry()
        from navig.tools.domains.system_pack import register_tools

        register_tools(reg)
        handler = reg.get_handler("system_info")
        result = handler()
        assert "platform" in result
        assert "python" in result
        assert result["python"] == platform.python_version()

    def test_json_parse_handler_valid(self):
        """json_parse handler parses valid JSON."""
        reg = _fresh_registry()
        from navig.tools.domains.data_pack import register_tools

        register_tools(reg)
        handler = reg.get_handler("json_parse")
        result = handler(text='{"key": "value"}')
        assert result == {"parsed": {"key": "value"}}

    def test_json_parse_handler_invalid(self):
        """json_parse handler returns error for invalid JSON."""
        reg = _fresh_registry()
        from navig.tools.domains.data_pack import register_tools

        register_tools(reg)
        handler = reg.get_handler("json_parse")
        result = handler(text="not json")
        assert "error" in result


# ===========================================================================
# Test 5: ToolResult Formatting
# ===========================================================================


class TestToolResultFormatting:
    """format_tool_result_for_llm() output."""

    def test_success_result_formatting(self):
        """Successful result is formatted with output."""
        result = ToolResult(
            tool="web_search",
            status=ToolResultStatus.SUCCESS,
            output={"results": [{"title": "Test"}]},
        )
        formatted = format_tool_result_for_llm(result)
        assert "web_search" in formatted
        assert "returned" in formatted
        assert "Test" in formatted

    def test_error_result_formatting(self):
        """Error result is formatted with status and error message."""
        result = ToolResult(
            tool="web_fetch",
            status=ToolResultStatus.ERROR,
            error="Connection timeout",
        )
        formatted = format_tool_result_for_llm(result)
        assert "web_fetch" in formatted
        assert "failed" in formatted
        assert "Connection timeout" in formatted

    def test_tool_result_to_dict(self):
        """ToolResult.to_dict() is JSON-serializable."""
        result = ToolResult(
            tool="test",
            status=ToolResultStatus.SUCCESS,
            output={"data": 42},
            latency_ms=15,
        )
        d = result.to_dict()
        assert d["tool"] == "test"
        assert d["status"] == "success"
        json.dumps(d)  # Must not raise


# ===========================================================================
# Test 6: Integration — Parse -> Route -> Execute -> Format
# ===========================================================================


class TestIntegration:
    """End-to-end: LLM output -> parsed action -> router execution -> formatted result."""

    def test_full_pipeline_tool_call(self):
        """Full pipeline: parse tool_call JSON -> execute -> format result."""
        reset_globals()
        reg = _fresh_registry()
        _register_echo(reg)
        router = ToolRouter(registry=reg)

        llm_text = '{"action": "tool_call", "tool": "echo_tool", "parameters": {"x": 42}}'
        action = parse_llm_action(llm_text)
        assert isinstance(action, ToolCallAction)

        result = router.execute(action)
        assert result.success
        assert result.output == {"echo": {"x": 42}}

        formatted = format_tool_result_for_llm(result)
        assert "echo_tool" in formatted
        assert "42" in formatted

    def test_full_pipeline_respond(self):
        """Full pipeline: plain text -> RespondAction (no tool call)."""
        llm_text = "The answer is 42."
        action = parse_llm_action(llm_text)
        assert isinstance(action, RespondAction)
        assert action.message == "The answer is 42."

    def test_full_pipeline_multi_step(self):
        """Full pipeline: multi_step -> execute all steps."""
        reg = _fresh_registry()
        _register_echo(reg, "tool_a")
        _register_echo(reg, "tool_b")
        router = ToolRouter(registry=reg)

        llm_text = json.dumps(
            {
                "action": "multi_step",
                "steps": [
                    {"tool": "tool_a", "parameters": {"step": 1}},
                    {"tool": "tool_b", "parameters": {"step": 2}},
                ],
            }
        )
        action = parse_llm_action(llm_text)
        assert isinstance(action, MultiStepAction)

        results = router.execute_multi(action.steps)
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_system_info_end_to_end(self):
        """End-to-end: system_info tool with real handler."""
        reg = _fresh_registry()
        from navig.tools.domains.system_pack import register_tools

        register_tools(reg)
        router = ToolRouter(registry=reg)

        action = ToolCallAction(tool="system_info")
        result = router.execute(action)
        assert result.success
        assert "python" in result.output
        assert result.output["python"] == platform.python_version()

    def test_config_schema_tools_section(self):
        """ToolsConfig in config_schema.py has correct defaults."""
        try:
            from navig.core.config_schema import ToolsConfig

            cfg = ToolsConfig()
            assert cfg.enabled is True
            assert cfg.max_calls_per_turn == 10
            assert cfg.blocked_tools == []
            assert cfg.safety_mode == "standard"
        except ImportError:
            pytest.skip("Pydantic not available")


class TestGlobalSingletons:
    """Concurrency and reset behavior for global tool singletons."""

    def test_get_tool_registry_threadsafe_singleton(self, monkeypatch):
        import navig.tools.router as router_mod

        router_mod.reset_globals()

        init_calls: list[int] = []
        init_calls_lock = threading.Lock()
        original_initialize = router_mod.ToolRegistry.initialize

        def _counted_initialize(self):
            with init_calls_lock:
                init_calls.append(1)
            time.sleep(0.01)
            return original_initialize(self)

        monkeypatch.setattr(router_mod.ToolRegistry, "initialize", _counted_initialize)

        with ThreadPoolExecutor(max_workers=16) as pool:
            instances = list(pool.map(lambda _i: router_mod.get_tool_registry(), range(48)))

        first = instances[0]
        assert all(instance is first for instance in instances)
        assert len(init_calls) == 1

    def test_get_tool_router_threadsafe_singleton(self, monkeypatch):
        import navig.tools.router as router_mod

        router_mod.reset_globals()

        ctor_calls: list[int] = []
        ctor_calls_lock = threading.Lock()
        original_router_cls = router_mod.ToolRouter

        class _CountingRouter(original_router_cls):
            def __init__(self, *args, **kwargs):
                with ctor_calls_lock:
                    ctor_calls.append(1)
                time.sleep(0.01)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(router_mod, "ToolRouter", _CountingRouter)

        with ThreadPoolExecutor(max_workers=16) as pool:
            instances = list(pool.map(lambda _i: router_mod.get_tool_router(), range(48)))

        first = instances[0]
        assert all(instance is first for instance in instances)
        assert len(ctor_calls) == 1

    def test_reset_globals_allows_fresh_reinitialization(self):
        import navig.tools.router as router_mod

        router_mod.reset_globals()

        first_registry = router_mod.get_tool_registry()
        first_router = router_mod.get_tool_router()

        router_mod.reset_globals()

        second_registry = router_mod.get_tool_registry()
        second_router = router_mod.get_tool_router()

        assert second_registry is not first_registry
        assert second_router is not first_router
