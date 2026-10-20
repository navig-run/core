"""Regression: a STREAMED turn must not throw away the model's tool call.

The operator sent their bot a bare TikTok link and got back:

    Agent reached the 1-turn limit without a final answer.
    Try a more specific request.

Nothing about that was true. The budget was 90 turns and exactly one ran. What
actually happened: a 33-character message is classified as "chat-feel", which
enables the streaming path — but the request still advertises every tool with
tool_choice="auto", so the model answered the link with a *tool call*. The
stream loop read only ``chunk.delta`` (text) and then hardcoded
``tool_calls=None``, so the decision to act was dropped on the floor. The turn
came back empty, the loop read that as "the model has nothing more to say", and
the generic fallback blamed a limit that was never reached.

These tests pin the whole chain: the tool runs, the answer comes back, and when
there genuinely is no answer the message says what really happened.
"""
import json
from types import SimpleNamespace

import pytest

# A short, single-token message — the shape that turns streaming on.
TIKTOK_LINK = "https://vm.tiktok.com/ZGdxryFmF"


def _fake_llm_config():
    return SimpleNamespace(
        provider="anthropic", model="claude-sonnet-5",
        temperature=0.2, max_tokens=512, base_url=None,
    )


def _apply_common_patches(monkeypatch, registry):
    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr("navig.llm.router.resolve_llm", lambda mode="coding": _fake_llm_config())
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())


class _Registry:
    """Records what actually got dispatched."""

    def __init__(self):
        self.dispatched = []

    def get_openai_schemas(self, toolsets):
        return [{
            "function": {
                "name": "download",
                "description": "download a link",
                "parameters": {"type": "object", "properties": {}},
            }
        }]

    def available_names(self, toolsets):
        return ["download"]

    def dispatch(self, name, args, vault_injector=None):
        self.dispatched.append((name, args))
        return "downloaded: clip.mp4"


def _chunk(**kw):
    from navig.providers.clients import StreamChunk
    return StreamChunk(**kw)


def _tool_fragment(name="", args="", tc_id="", index=0):
    from navig.providers.clients import StreamChunk, ToolCall
    return StreamChunk(
        tool_call_delta=ToolCall(id=tc_id, name=name, arguments=args),
        tool_call_index=index,
    )


class _StreamingClient:
    """Streams the scripted turns; `complete()` serves the same script."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.complete_calls = 0

    async def complete_stream(self, request):
        for chunk in self._turns.pop(0):
            yield chunk

    async def complete(self, request):
        self.complete_calls += 1
        chunks = self._turns.pop(0)
        from navig.providers.clients import CompletionResponse
        from navig.providers.clients import merge_tool_call_deltas as _merge
        text = "".join(c.delta for c in chunks if getattr(c, "delta", None))
        return CompletionResponse(
            content=text or None,
            tool_calls=_merge(chunks) or None,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


async def _noop_partial(_text: str) -> None:
    return None


def _install(monkeypatch, client):
    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kw: client,
    )


@pytest.mark.asyncio
async def test_streamed_tool_call_is_dispatched_not_dropped(monkeypatch):
    """The reported bug, end to end: link in → tool runs → real answer out."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    registry = _Registry()
    _apply_common_patches(monkeypatch, registry)

    client = _StreamingClient([
        # Turn 1 — the model answers the link with a tool call, no text at all.
        [
            _tool_fragment(name="download", tc_id="call_a", index=0),
            _tool_fragment(args=json.dumps({"url": TIKTOK_LINK}), index=0),
            _chunk(finish_reason="tool_calls"),
        ],
        # Turn 2 — with the tool result in hand it writes the reply.
        [_chunk(delta="Here's the clip"), _chunk(finish_reason="stop")],
    ])
    _install(monkeypatch, client)

    agent = ConvAgent()
    reply = await agent.run_agentic(message=TIKTOK_LINK, on_partial=_noop_partial)

    assert registry.dispatched == [("download", {"url": TIKTOK_LINK})], (
        "the streamed tool call never reached dispatch"
    )
    assert reply == "Here's the clip"
    assert "turn limit" not in reply


@pytest.mark.asyncio
async def test_streamed_turn_never_reports_a_limit_it_did_not_reach(monkeypatch):
    """The exact string the operator saw must not come back for one empty turn."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    _apply_common_patches(monkeypatch, _Registry())
    # Both the streamed turn AND the unstreamed retry come back empty.
    client = _StreamingClient([[_chunk(finish_reason="stop")], []])
    _install(monkeypatch, client)

    agent = ConvAgent()
    reply = await agent.run_agentic(message=TIKTOK_LINK, on_partial=_noop_partial)

    assert "turn limit" not in reply, f"claimed a limit that was never reached: {reply!r}"
    assert "1-turn" not in reply
    # It should name what really happened instead.
    assert "empty response" in reply


@pytest.mark.asyncio
async def test_empty_stream_retries_unstreamed_once(monkeypatch):
    """A stream yielding nothing is a dead end — recover instead of surfacing it."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    _apply_common_patches(monkeypatch, _Registry())
    client = _StreamingClient([
        [],                                      # streamed: nothing at all
        [_chunk(delta="recovered answer")],      # unstreamed retry: the answer
    ])
    _install(monkeypatch, client)

    agent = ConvAgent()
    reply = await agent.run_agentic(message=TIKTOK_LINK, on_partial=_noop_partial)

    assert client.complete_calls == 1, "the empty stream was not retried unstreamed"
    assert reply == "recovered answer"


@pytest.mark.asyncio
async def test_streamed_text_answer_still_streams_to_on_partial(monkeypatch):
    """The streaming UX must be untouched when the model replies with text."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    _apply_common_patches(monkeypatch, _Registry())
    client = _StreamingClient([
        [_chunk(delta="Hel"), _chunk(delta="lo"), _chunk(finish_reason="stop")],
    ])
    _install(monkeypatch, client)

    seen = []

    async def _capture(text: str) -> None:
        seen.append(text)

    agent = ConvAgent()
    reply = await agent.run_agentic(message="hey", on_partial=_capture)

    assert reply == "Hello"
    assert seen == ["Hel", "Hello"]
    assert client.complete_calls == 0, "a text answer must not trigger the retry"


@pytest.mark.asyncio
async def test_parallel_streamed_tool_calls_both_dispatch(monkeypatch):
    """Two calls in one streamed turn must not merge into one mangled call."""
    from navig.agent.conv import ConversationalAgent as ConvAgent

    registry = _Registry()
    _apply_common_patches(monkeypatch, registry)

    client = _StreamingClient([
        [
            _tool_fragment(name="download", tc_id="call_a", index=0),
            _tool_fragment(args='{"url": "a"}', index=0),
            _tool_fragment(name="download", tc_id="call_b", index=1),
            _tool_fragment(args='{"url": "b"}', index=1),
            _chunk(finish_reason="tool_calls"),
        ],
        [_chunk(delta="both done"), _chunk(finish_reason="stop")],
    ])
    _install(monkeypatch, client)

    agent = ConvAgent()
    reply = await agent.run_agentic(message=TIKTOK_LINK, on_partial=_noop_partial)

    assert registry.dispatched == [
        ("download", {"url": "a"}),
        ("download", {"url": "b"}),
    ]
    assert reply == "both done"


# A long message (>= 12 words) — NOT "chat-feel". Before the streaming gate was
# decoupled from _short_chat, this took the blocking complete() path and no
# token reached on_partial until the whole turn was done.
LONG_MESSAGE = (
    "please write me a careful step by step explanation of how this works in detail"
)


def _skip_heavy_context(monkeypatch, conv_cls):
    """Stub the non-short-chat context builders so the test stays hermetic.

    A long message runs plan-context gather + skills + recall; none of that is
    under test here and all of it does disk I/O, so neutralise it.
    """
    monkeypatch.setattr(conv_cls, "_get_plan_context_block", lambda self: "")
    monkeypatch.setattr(conv_cls, "_build_skills_section", lambda self, message: "")
    monkeypatch.setattr(conv_cls, "_recall_block", lambda self, message: "")


@pytest.mark.asyncio
async def test_long_message_now_streams_to_on_partial(monkeypatch):
    """The core win: a substantive (non-short) turn streams, it no longer blocks.

    `assert len(LONG_MESSAGE.split()) >= 12` keeps this honest — if someone
    shortens the fixture it would silently fall back onto the always-streamed
    short-chat path and stop testing the decoupled gate.
    """
    from navig.agent.conv import ConversationalAgent as ConvAgent

    assert len(LONG_MESSAGE.split()) >= 12, "fixture must be a non-short message"
    _apply_common_patches(monkeypatch, _Registry())
    _skip_heavy_context(monkeypatch, ConvAgent)
    client = _StreamingClient([
        [_chunk(delta="Sure"), _chunk(delta=", here"), _chunk(finish_reason="stop")],
    ])
    _install(monkeypatch, client)

    seen = []

    async def _capture(text: str) -> None:
        seen.append(text)

    agent = ConvAgent()
    reply = await agent.run_agentic(message=LONG_MESSAGE, on_partial=_capture)

    assert reply == "Sure, here"
    assert seen == ["Sure", "Sure, here"], "a long turn must stream incrementally"
    assert client.complete_calls == 0, "a streamed text answer must not block on complete()"


@pytest.mark.asyncio
async def test_preamble_streams_but_freezes_once_a_tool_call_begins(monkeypatch):
    """Text before a tool call is shown; text after it is not surfaced live.

    This is the half-answer-flicker guard: the user sees "Let me check that",
    then the bubble stops editing while the tool runs, so the post-tool text the
    model emits in the same round is never shown and then retracted. It is still
    kept in the turn's content.
    """
    from navig.agent.conv import ConversationalAgent as ConvAgent

    registry = _Registry()
    _apply_common_patches(monkeypatch, registry)
    _skip_heavy_context(monkeypatch, ConvAgent)
    client = _StreamingClient([
        [
            _chunk(delta="Let me "),
            _chunk(delta="check that"),
            _tool_fragment(name="download", tc_id="call_a", index=0),
            _tool_fragment(args='{"url": "x"}', index=0),
            _chunk(delta="POST-TOOL-TEXT"),      # arrives after the tool call
            _chunk(finish_reason="tool_calls"),
        ],
        [_chunk(delta="done"), _chunk(finish_reason="stop")],
    ])
    _install(monkeypatch, client)

    seen = []

    async def _capture(text: str) -> None:
        seen.append(text)

    agent = ConvAgent()
    reply = await agent.run_agentic(message=LONG_MESSAGE, on_partial=_capture)

    assert registry.dispatched == [("download", {"url": "x"})]
    assert reply == "done"
    # The preamble streams; then the bubble freezes for the rest of turn 1; then
    # turn 2's final answer streams on its own (the guard is per-round, so a
    # final no-tool round is never suppressed). The post-tool text of turn 1 is
    # the one thing that must never surface.
    assert seen == ["Let me ", "Let me check that", "done"], seen
    assert all("POST-TOOL-TEXT" not in text for text in seen), (
        "text emitted after the tool call must not reach the live bubble"
    )
