"""
Batch 16: Tests for
- navig.tools.schemas (ActionType, ToolResultStatus, ToolCallAction, RespondAction,
  MultiStepAction, ToolResult, _find_bare_json_objects, parse_llm_action,
  format_tool_result_for_llm)
- navig.messaging.adapter_registry (AdapterRegistryManager register/get/enable/disable)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# navig.tools.schemas
# ---------------------------------------------------------------------------
from navig.tools.schemas import (
    ActionType,
    MultiStepAction,
    RespondAction,
    ToolCallAction,
    ToolResult,
    ToolResultStatus,
    _find_bare_json_objects,
    format_tool_result_for_llm,
    parse_llm_action,
)


class TestActionTypeEnum:
    def test_tool_call_value(self):
        assert ActionType.TOOL_CALL.value == "tool_call"

    def test_respond_value(self):
        assert ActionType.RESPOND.value == "respond"

    def test_multi_step_value(self):
        assert ActionType.MULTI_STEP.value == "multi_step"


class TestToolResultStatusEnum:
    def test_success_value(self):
        assert ToolResultStatus.SUCCESS.value == "success"

    def test_error_value(self):
        assert ToolResultStatus.ERROR.value == "error"

    def test_timeout_denied_not_found(self):
        assert ToolResultStatus.TIMEOUT.value == "timeout"
        assert ToolResultStatus.DENIED.value == "denied"
        assert ToolResultStatus.NOT_FOUND.value == "not_found"


class TestToolCallAction:
    def test_basic(self):
        a = ToolCallAction(tool="web_search", parameters={"query": "python"})
        assert a.tool == "web_search"
        assert a.parameters["query"] == "python"

    def test_action_type(self):
        a = ToolCallAction(tool="x")
        assert a.action_type == ActionType.TOOL_CALL

    def test_defaults(self):
        a = ToolCallAction(tool="t")
        assert a.parameters == {}
        assert a.reason == ""
        assert a.request_id == ""


class TestRespondAction:
    def test_basic(self):
        a = RespondAction(message="Hello!")
        assert a.message == "Hello!"

    def test_action_type(self):
        a = RespondAction(message="")
        assert a.action_type == ActionType.RESPOND


class TestMultiStepAction:
    def test_basic(self):
        steps = [ToolCallAction(tool="a"), ToolCallAction(tool="b")]
        a = MultiStepAction(steps=steps)
        assert len(a.steps) == 2

    def test_action_type(self):
        a = MultiStepAction()
        assert a.action_type == ActionType.MULTI_STEP

    def test_defaults(self):
        a = MultiStepAction()
        assert a.steps == []
        assert a.reason == ""


class TestToolResult:
    def test_success_true(self):
        r = ToolResult(tool="web_search", status=ToolResultStatus.SUCCESS, output={"data": 1})
        assert r.success is True

    def test_success_false_on_error(self):
        r = ToolResult(tool="t", status=ToolResultStatus.ERROR, error="boom")
        assert r.success is False

    def test_to_dict_keys(self):
        r = ToolResult(tool="t", output="result")
        d = r.to_dict()
        assert "tool" in d
        assert "status" in d
        assert "output" in d
        assert "error" in d
        assert "latency_ms" in d

    def test_to_dict_status_is_string(self):
        r = ToolResult(tool="t", status=ToolResultStatus.SUCCESS)
        assert r.to_dict()["status"] == "success"


class TestFindBareJsonObjects:
    def test_single_object(self):
        text = '{"action": "respond", "message": "hi"}'
        results = _find_bare_json_objects(text)
        assert len(results) == 1
        assert json.loads(results[0])["action"] == "respond"

    def test_nested_braces(self):
        text = '{"outer": {"inner": true}}'
        results = _find_bare_json_objects(text)
        assert len(results) == 1
        obj = json.loads(results[0])
        assert obj["outer"]["inner"] is True

    def test_multiple_objects(self):
        text = '{"a": 1} {"b": 2}'
        results = _find_bare_json_objects(text)
        assert len(results) == 2

    def test_no_objects(self):
        assert _find_bare_json_objects("hello world") == []

    def test_braces_inside_string_ignored(self):
        text = '{"key": "value with {braces}"}'
        results = _find_bare_json_objects(text)
        assert len(results) == 1


class TestParseLlmAction:
    def test_empty_string_returns_respond(self):
        result = parse_llm_action("")
        assert isinstance(result, RespondAction)
        assert result.message == ""

    def test_plain_text_returns_respond(self):
        result = parse_llm_action("Hello, how can I help?")
        assert isinstance(result, RespondAction)
        assert "Hello" in result.message

    def test_tool_call_json(self):
        text = '{"action": "tool_call", "tool": "web_search", "parameters": {"query": "python"}}'
        result = parse_llm_action(text)
        assert isinstance(result, ToolCallAction)
        assert result.tool == "web_search"
        assert result.parameters["query"] == "python"

    def test_respond_json(self):
        text = '{"action": "respond", "message": "Here is the answer"}'
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)
        assert result.message == "Here is the answer"

    def test_multi_step_json(self):
        text = json.dumps({
            "action": "multi_step",
            "steps": [
                {"tool": "web_search", "parameters": {"query": "x"}},
                {"tool": "summarize", "parameters": {}},
            ]
        })
        result = parse_llm_action(text)
        assert isinstance(result, MultiStepAction)
        assert len(result.steps) == 2
        assert result.steps[0].tool == "web_search"

    def test_fenced_json_block(self):
        text = '```json\n{"action": "respond", "message": "fenced"}\n```'
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)
        assert result.message == "fenced"

    def test_tool_call_missing_tool_falls_back_to_respond(self):
        text = '{"action": "tool_call"}'  # no "tool" field
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)

    def test_multi_step_no_valid_steps_falls_back(self):
        text = '{"action": "multi_step", "steps": []}'
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)

    def test_json_with_tool_key_no_action(self):
        text = '{"tool": "image_generate", "parameters": {"prompt": "cat"}}'
        result = parse_llm_action(text)
        assert isinstance(result, ToolCallAction)
        assert result.tool == "image_generate"

    def test_unknown_action_falls_back_to_respond(self):
        text = '{"action": "fly_to_moon"}'
        result = parse_llm_action(text)
        assert isinstance(result, RespondAction)


class TestFormatToolResultForLlm:
    def test_success_format(self):
        r = ToolResult(tool="web_search", output={"title": "Python"})
        text = format_tool_result_for_llm(r)
        assert "web_search" in text
        assert "Python" in text

    def test_error_format(self):
        r = ToolResult(tool="db.query", status=ToolResultStatus.ERROR, error="timeout")
        text = format_tool_result_for_llm(r)
        assert "failed" in text.lower()
        assert "timeout" in text


# ---------------------------------------------------------------------------
# navig.messaging.adapter_registry — AdapterRegistryManager
# ---------------------------------------------------------------------------
from navig.messaging.adapter import ComplianceMode
from navig.messaging.adapter_registry import AdapterRegistryManager


def _make_mock_adapter(name: str, compliance: str = "official") -> MagicMock:
    adapter = MagicMock()
    adapter.name = name
    adapter.compliance = compliance
    adapter.identity_mode = "bot"
    adapter.capabilities = ["send", "receive"]
    return adapter


class TestAdapterRegistryManager:
    def setup_method(self):
        self.registry = AdapterRegistryManager()

    def test_register_and_is_registered(self):
        adapter = _make_mock_adapter("sms")
        self.registry.register(adapter)
        assert self.registry.is_registered("sms")

    def test_case_insensitive_registration(self):
        adapter = _make_mock_adapter("SMS")
        self.registry.register(adapter)
        assert self.registry.is_registered("sms")

    def test_official_adapter_enabled_by_default(self):
        adapter = _make_mock_adapter("sms", "official")
        self.registry.register(adapter, compliance=ComplianceMode.OFFICIAL)
        assert self.registry.is_available("sms")

    def test_experimental_adapter_disabled_by_default(self):
        adapter = _make_mock_adapter("wa_web", "experimental")
        self.registry.register(adapter, compliance=ComplianceMode.EXPERIMENTAL)
        assert not self.registry.is_available("wa_web")

    def test_get_returns_none_for_disabled(self):
        adapter = _make_mock_adapter("exp", "experimental")
        self.registry.register(adapter, compliance=ComplianceMode.EXPERIMENTAL)
        assert self.registry.get("exp") is None

    def test_get_unchecked_returns_disabled_adapter(self):
        adapter = _make_mock_adapter("exp", "experimental")
        self.registry.register(adapter, compliance=ComplianceMode.EXPERIMENTAL)
        assert self.registry.get_unchecked("exp") is adapter

    def test_enable_adapter(self):
        adapter = _make_mock_adapter("exp", "experimental")
        self.registry.register(adapter, compliance=ComplianceMode.EXPERIMENTAL)
        self.registry.enable("exp")
        assert self.registry.is_available("exp")

    def test_disable_adapter(self):
        adapter = _make_mock_adapter("sms", "official")
        self.registry.register(adapter)
        self.registry.disable("sms")
        assert not self.registry.is_available("sms")

    def test_enable_returns_false_if_not_registered(self):
        assert self.registry.enable("phantom") is False

    def test_unregister(self):
        adapter = _make_mock_adapter("sms")
        self.registry.register(adapter)
        self.registry.unregister("sms")
        assert not self.registry.is_registered("sms")

    def test_available_names(self):
        a1 = _make_mock_adapter("sms", "official")
        a2 = _make_mock_adapter("discord", "official")
        a3 = _make_mock_adapter("exp", "experimental")
        self.registry.register(a1)
        self.registry.register(a2)
        self.registry.register(a3, compliance=ComplianceMode.EXPERIMENTAL)
        names = self.registry.available_names()
        assert "sms" in names
        assert "discord" in names
        assert "exp" not in names

    def test_apply_config_enable(self):
        adapter = _make_mock_adapter("discord", "official")
        self.registry.register(adapter)
        self.registry.disable("discord")
        self.registry.apply_config({"discord": {"enabled": True}})
        assert self.registry.is_available("discord")

    def test_apply_config_disable(self):
        adapter = _make_mock_adapter("sms", "official")
        self.registry.register(adapter)
        self.registry.apply_config({"sms": {"enabled": False}})
        assert not self.registry.is_available("sms")

    def test_get_compliance(self):
        adapter = _make_mock_adapter("sms", "official")
        self.registry.register(adapter, compliance=ComplianceMode.OFFICIAL)
        assert self.registry.get_compliance("sms") == ComplianceMode.OFFICIAL
