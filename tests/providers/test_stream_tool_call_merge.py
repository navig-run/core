"""Regression: a streamed tool call must survive the wire AND the reassembly.

When a model answers with a tool call instead of text, it streams that decision
as tool-call fragments. Nothing in the tree ever read them: the provider clients
built ``StreamChunk.tool_call_delta`` faithfully and every consumer looked only
at ``chunk.delta``, so a streamed turn that chose a tool arrived completely
empty — no text, no calls — and the agent loop reported that as a turn limit.

These tests pin both halves: the wire shapes each provider actually emits, and
the merge that turns fragments back into whole :class:`ToolCall` objects.
"""
from types import SimpleNamespace

import pytest

from navig.providers.clients import (
    StreamChunk,
    ToolCall,
    merge_tool_call_deltas,
)


def _frag(name="", args="", tc_id="", index=None):
    return StreamChunk(
        tool_call_delta=ToolCall(id=tc_id, name=name, arguments=args),
        tool_call_index=index,
    )


# ─────────────────────────────────────────────────────────────
# merge_tool_call_deltas — the shapes real providers emit
# ─────────────────────────────────────────────────────────────


class TestMergeToolCallDeltas:
    def test_openai_shape_id_and_name_only_on_first_fragment(self):
        """OpenAI names the call once, then streams arguments with a blank id."""
        merged = merge_tool_call_deltas([
            _frag(name="download", tc_id="call_a", index=0),
            _frag(args='{"url":', index=0),
            _frag(args='"https://vm.tiktok.com/X"}', index=0),
        ])
        assert len(merged) == 1
        assert merged[0].id == "call_a"
        assert merged[0].name == "download"
        assert merged[0].arguments == '{"url":"https://vm.tiktok.com/X"}'

    def test_anthropic_shape_repeats_id_and_name_every_fragment(self):
        merged = merge_tool_call_deltas([
            _frag(name="download", tc_id="toolu_1", args="", index=1),
            _frag(name="download", tc_id="toolu_1", args='{"url"', index=1),
            _frag(name="download", tc_id="toolu_1", args=':"x"}', index=1),
        ])
        assert len(merged) == 1
        assert merged[0].name == "download"
        assert merged[0].arguments == '{"url":"x"}'

    def test_parallel_calls_are_kept_apart_by_index(self):
        """Without the index the second call's arguments append to the first."""
        merged = merge_tool_call_deltas([
            _frag(name="search", tc_id="call_a", index=0),
            _frag(args='{"q":"a"}', index=0),
            _frag(name="fetch", tc_id="call_b", index=1),
            _frag(args='{"u":"b"}', index=1),
        ])
        assert [(c.name, c.arguments) for c in merged] == [
            ("search", '{"q":"a"}'),
            ("fetch", '{"u":"b"}'),
        ]

    def test_interleaved_parallel_calls_route_by_index(self):
        merged = merge_tool_call_deltas([
            _frag(name="search", tc_id="call_a", index=0),
            _frag(name="fetch", tc_id="call_b", index=1),
            _frag(args='{"q":', index=0),
            _frag(args='{"u":', index=1),
            _frag(args='"a"}', index=0),
            _frag(args='"b"}', index=1),
        ])
        assert [(c.name, c.arguments) for c in merged] == [
            ("search", '{"q":"a"}'),
            ("fetch", '{"u":"b"}'),
        ]

    def test_zero_argument_call_survives(self):
        """A no-arg tool streams a name and never a single argument fragment."""
        merged = merge_tool_call_deltas([_frag(name="status", tc_id="call_a", index=0)])
        assert len(merged) == 1
        assert merged[0].name == "status"
        assert merged[0].arguments == ""

    def test_continuation_without_index_or_id_appends_to_open_call(self):
        """Providers that send neither index nor a repeated id still reassemble."""
        merged = merge_tool_call_deltas([
            _frag(name="search", tc_id="call_a"),
            _frag(args='{"q":'),
            _frag(args='"x"}'),
        ])
        assert len(merged) == 1
        assert merged[0].arguments == '{"q":"x"}'

    def test_text_chunks_are_ignored(self):
        merged = merge_tool_call_deltas([
            StreamChunk(delta="hello"),
            StreamChunk(delta=" world", finish_reason="stop"),
        ])
        assert merged == []

    def test_nameless_fragments_are_dropped_not_dispatched(self):
        """An unroutable call must not reach dispatch as a nameless tool."""
        assert merge_tool_call_deltas([_frag(args='{"q":"x"}', index=0)]) == []

    def test_later_fragment_never_overwrites_the_opening_id_or_name(self):
        merged = merge_tool_call_deltas([
            _frag(name="search", tc_id="call_a", index=0),
            _frag(name="", tc_id="", args="{}", index=0),
        ])
        assert merged[0].id == "call_a"
        assert merged[0].name == "search"

    def test_empty_input(self):
        assert merge_tool_call_deltas([]) == []


# ─────────────────────────────────────────────────────────────
# The wire: what each client emits for a streamed tool call
# ─────────────────────────────────────────────────────────────


class _FakeSSEResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):  # pragma: no cover — only on the error path
        return b""


class _FakeHTTPClient:
    """Stands in for the httpx client: `stream(...)` is an async CM."""

    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kwargs):
        lines = self._lines

        class _CM:
            async def __aenter__(self):
                return _FakeSSEResponse(lines)

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _async(value):
    """An awaitable resolving to *value* — stands in for ``_get_client()``."""
    async def _await():
        return value

    return _await()


def _config():
    return SimpleNamespace(
        name="test", base_url="https://example.invalid/v1", api_key=None,
        models=[], default_model="m", api_type="openai",
    )


async def _collect(client, request):
    return [chunk async for chunk in client.complete_stream(request)]


@pytest.mark.asyncio
async def test_openai_stream_emits_every_parallel_tool_call(monkeypatch):
    """A single delta can carry several calls; reading only [0] dropped the rest."""
    from navig.providers.clients import CompletionRequest, Message, OpenAIClient

    lines = [
        'data: {"choices":[{"delta":{"tool_calls":['
        '{"index":0,"id":"call_a","function":{"name":"search","arguments":""}},'
        '{"index":1,"id":"call_b","function":{"name":"fetch","arguments":""}}'
        ']}}]}',
        'data: {"choices":[{"delta":{"tool_calls":['
        '{"index":0,"function":{"arguments":"{\\"q\\":\\"x\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    client = OpenAIClient(_config())
    monkeypatch.setattr(client, "_get_client", lambda: _async(_FakeHTTPClient(lines)))

    chunks = await _collect(
        client,
        CompletionRequest(messages=[Message(role="user", content="hi")], model="m"),
    )
    merged = merge_tool_call_deltas(chunks)
    assert [(c.name, c.arguments) for c in merged] == [
        ("search", '{"q":"x"}'),
        ("fetch", ""),
    ]


@pytest.mark.asyncio
async def test_anthropic_stream_emits_a_zero_argument_tool_call(monkeypatch):
    """A tool_use block with no input_json_delta used to vanish entirely."""
    from navig.providers.clients import AnthropicClient, CompletionRequest, Message

    lines = [
        'data: {"type":"message_start","message":{"model":"claude","usage":{}}}',
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"toolu_1","name":"status"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{}}',
    ]
    client = AnthropicClient(_config())
    monkeypatch.setattr(client, "_get_client", lambda: _async(_FakeHTTPClient(lines)))

    chunks = await _collect(
        client,
        CompletionRequest(messages=[Message(role="user", content="hi")], model="m"),
    )
    merged = merge_tool_call_deltas(chunks)
    assert len(merged) == 1
    assert merged[0].name == "status"
    assert merged[0].id == "toolu_1"


@pytest.mark.asyncio
async def test_base_stream_fallback_carries_tool_calls_through():
    """Providers without their own SSE stream fall back to complete() — and that
    fallback used to drop the tool calls complete() had already returned."""
    from navig.providers.clients import (
        BaseProviderClient,
        CompletionRequest,
        CompletionResponse,
        Message,
    )

    class _NoSSEClient(BaseProviderClient):
        async def complete(self, request):
            return CompletionResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="call_a", name="download", arguments='{"url":"x"}'),
                    ToolCall(id="call_b", name="search", arguments='{"q":"y"}'),
                ],
                finish_reason="tool_calls",
                provider="test",
            )

        def get_available_models(self):  # pragma: no cover — unused here
            return []

    client = _NoSSEClient(_config())
    chunks = await _collect(
        client,
        CompletionRequest(messages=[Message(role="user", content="hi")], model="m"),
    )
    merged = merge_tool_call_deltas(chunks)
    assert [(c.name, c.arguments) for c in merged] == [
        ("download", '{"url":"x"}'),
        ("search", '{"q":"y"}'),
    ]


@pytest.mark.asyncio
async def test_anthropic_stream_merges_argument_fragments(monkeypatch):
    from navig.providers.clients import AnthropicClient, CompletionRequest, Message

    lines = [
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"toolu_1","name":"download"}}',
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"url\\":"}}',
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"\\"tt\\"}"}}',
    ]
    client = AnthropicClient(_config())
    monkeypatch.setattr(client, "_get_client", lambda: _async(_FakeHTTPClient(lines)))

    chunks = await _collect(
        client,
        CompletionRequest(messages=[Message(role="user", content="hi")], model="m"),
    )
    merged = merge_tool_call_deltas(chunks)
    assert len(merged) == 1
    assert merged[0].arguments == '{"url":"tt"}'
