"""
Hermetic unit tests for navig.providers.clients

Covers:
- Message dataclass defaults
- ToolDefinition.to_openai_format / to_anthropic_format
- CompletionRequest defaults
- CompletionResponse defaults and has_tool_calls property
- ProviderError str representation
- StreamChunk defaults
- ToolCall dataclass fields
"""

import pytest

from navig.providers.clients import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderError,
    StreamChunk,
    ToolCall,
    ToolDefinition,
)
from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE


# ─────────────────────────────────────────────────────────────
# Message
# ─────────────────────────────────────────────────────────────


class TestMessage:
    def test_basic_fields(self):
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_optional_defaults_none(self):
        msg = Message(role="assistant", content="Hi")
        assert msg.name is None
        assert msg.tool_call_id is None
        assert msg.tool_calls is None

    def test_tool_message(self):
        msg = Message(
            role="tool",
            content="result",
            tool_call_id="call-001",
            name="run_command",
        )
        assert msg.tool_call_id == "call-001"
        assert msg.name == "run_command"


# ─────────────────────────────────────────────────────────────
# ToolDefinition
# ─────────────────────────────────────────────────────────────


class TestToolDefinition:
    def _make(self):
        return ToolDefinition(
            name="run_cmd",
            description="Runs a shell command",
            parameters={
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        )

    def test_to_openai_format_keys(self):
        d = self._make().to_openai_format()
        assert d["type"] == "function"
        func = d["function"]
        assert func["name"] == "run_cmd"
        assert func["description"] == "Runs a shell command"
        assert "parameters" in func

    def test_to_openai_format_parameters_intact(self):
        d = self._make().to_openai_format()
        assert d["function"]["parameters"]["type"] == "object"

    def test_to_anthropic_format_keys(self):
        d = self._make().to_anthropic_format()
        assert d["name"] == "run_cmd"
        assert d["description"] == "Runs a shell command"
        assert "input_schema" in d

    def test_to_anthropic_format_input_schema(self):
        d = self._make().to_anthropic_format()
        assert d["input_schema"]["type"] == "object"

    def test_both_formats_use_same_params(self):
        td = self._make()
        openai = td.to_openai_format()["function"]["parameters"]
        anthropic = td.to_anthropic_format()["input_schema"]
        assert openai == anthropic


# ─────────────────────────────────────────────────────────────
# CompletionRequest
# ─────────────────────────────────────────────────────────────


class TestCompletionRequest:
    def _make(self, **kwargs):
        defaults = dict(
            messages=[Message(role="user", content="hi")],
            model="claude-3-5-sonnet-20241022",
        )
        defaults.update(kwargs)
        return CompletionRequest(**defaults)

    def test_defaults(self):
        req = self._make()
        assert req.temperature == _DEFAULT_TEMPERATURE
        assert req.max_tokens == _DEFAULT_MAX_TOKENS
        assert req.tools is None
        assert req.stream is False
        assert req.stop is None
        assert req.extra_body is None
        assert req.cache_control is False

    def test_custom_temperature(self):
        req = self._make(temperature=0.9)
        assert req.temperature == 0.9

    def test_stream_flag(self):
        req = self._make(stream=True)
        assert req.stream is True

    def test_tools_assigned(self):
        tools = [ToolDefinition("t", "desc", {})]
        req = self._make(tools=tools)
        assert len(req.tools) == 1


# ─────────────────────────────────────────────────────────────
# ToolCall
# ─────────────────────────────────────────────────────────────


class TestToolCall:
    def test_fields(self):
        tc = ToolCall(id="call-1", name="run_cmd", arguments='{"cmd": "ls"}')
        assert tc.id == "call-1"
        assert tc.name == "run_cmd"
        assert tc.arguments == '{"cmd": "ls"}'


# ─────────────────────────────────────────────────────────────
# CompletionResponse
# ─────────────────────────────────────────────────────────────


class TestCompletionResponse:
    def test_defaults_all_none(self):
        resp = CompletionResponse()
        assert resp.content is None
        assert resp.tool_calls is None
        assert resp.finish_reason is None
        assert resp.usage is None
        assert resp.model is None
        assert resp.provider is None

    def test_cache_fields_default_zero(self):
        resp = CompletionResponse()
        assert resp.cache_read_input_tokens == 0
        assert resp.cache_creation_input_tokens == 0

    def test_has_tool_calls_false_when_none(self):
        resp = CompletionResponse()
        assert resp.has_tool_calls is False

    def test_has_tool_calls_false_when_empty_list(self):
        resp = CompletionResponse(tool_calls=[])
        assert resp.has_tool_calls is False

    def test_has_tool_calls_true(self):
        tc = ToolCall(id="c1", name="fn", arguments="{}")
        resp = CompletionResponse(tool_calls=[tc])
        assert resp.has_tool_calls is True

    def test_content_and_finish_reason(self):
        resp = CompletionResponse(content="Done", finish_reason="stop")
        assert resp.content == "Done"
        assert resp.finish_reason == "stop"


# ─────────────────────────────────────────────────────────────
# ProviderError
# ─────────────────────────────────────────────────────────────


class TestProviderError:
    def test_str_full(self):
        e = ProviderError(
            message="unauthorized",
            status_code=401,
            provider="anthropic",
        )
        s = str(e)
        assert "anthropic" in s
        assert "unauthorized" in s
        assert "401" in s

    def test_defaults(self):
        e = ProviderError(message="oops")
        assert e.status_code is None
        assert e.error_type is None
        assert e.provider is None
        assert e.retryable is False

    def test_retryable_rate_limit(self):
        e = ProviderError(message="rate limit", error_type="rate_limit", retryable=True)
        assert e.retryable is True
        assert e.error_type == "rate_limit"

    def test_is_exception_subclass(self):
        e = ProviderError(message="bad")
        assert isinstance(e, Exception)


# ─────────────────────────────────────────────────────────────
# StreamChunk
# ─────────────────────────────────────────────────────────────


class TestStreamChunk:
    def test_defaults_all_none(self):
        chunk = StreamChunk()
        assert chunk.delta is None
        assert chunk.tool_call_delta is None
        assert chunk.finish_reason is None
        assert chunk.usage is None
        assert chunk.model is None
        assert chunk.provider is None

    def test_delta_chunk(self):
        chunk = StreamChunk(delta="Hello", provider="anthropic")
        assert chunk.delta == "Hello"
        assert chunk.provider == "anthropic"

    def test_finish_chunk(self):
        chunk = StreamChunk(finish_reason="stop", usage={"input_tokens": 10})
        assert chunk.finish_reason == "stop"
        assert chunk.usage["input_tokens"] == 10
