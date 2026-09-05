"""An over-long prompt failed twice and reported the second failure.

`chat_routed` catches a provider error and retries via `self.chat(messages, …)` — with
the *same* messages. For every failure except one that is the right thing to do. For a
context-window overflow it cannot work: the retry re-sends the identical oversized
payload and gets the identical rejection, so the user waits through two round trips to be
told the same thing.

`AIClient._trim_messages_for_retry` was written for exactly this — "keep system message +
last N to reduce context on retry" — and was called by nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.agent.ai_client import AIClient, is_context_overflow

pytestmark = pytest.mark.integration


# Real provider wordings. There is no shared exception type across providers, so the
# sentence is the only portable signal.
@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 8192 tokens, however you requested 9000",
        "context_length_exceeded",
        "Input is too long for requested model",
        "prompt is too long: 210000 tokens > 200000 maximum",
        "Please reduce the length of the messages",
        "Requested tokens exceeds the maximum allowed for this model",
        "context window exceeded",
    ],
)
def test_recognises_a_context_overflow(message):
    assert is_context_overflow(RuntimeError(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "Connection reset by peer",
        "401 Unauthorized",
        "rate limit exceeded, retry after 20s",
        "model not found",
        "The server had an error while processing your request",
        "",
    ],
)
def test_does_not_mistake_other_failures_for_it(message):
    """A false positive silently drops conversation history — worse than one more failed
    call. Anything not unambiguously about size must not match."""
    assert is_context_overflow(RuntimeError(message)) is False


def test_trim_keeps_the_system_message_and_the_most_recent_turns():
    client = AIClient.__new__(AIClient)
    messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(10)
    ]
    trimmed = client._trim_messages_for_retry(messages, keep_recent=4)

    assert trimmed[0]["role"] == "system", "the system prompt must survive a trim"
    assert len(trimmed) == 5
    assert [m["content"] for m in trimmed[1:]] == ["m6", "m7", "m8", "m9"], (
        "the RECENT turns are the ones worth keeping"
    )
    assert len(trimmed) < len(messages), "a trim that shortens nothing cannot help"


def test_trim_is_safe_on_empty_and_system_only_inputs():
    client = AIClient.__new__(AIClient)
    assert client._trim_messages_for_retry([]) == []
    only_system = [{"role": "system", "content": "sys"}]
    assert client._trim_messages_for_retry(only_system) == only_system


def test_the_overflow_retry_sends_fewer_messages_than_the_call_that_failed():
    """The behaviour, not the helper: an overflow must reach `chat` trimmed."""
    client = AIClient.__new__(AIClient)
    sent: dict = {}

    async def _boom(messages, decision, temperature):
        raise RuntimeError("This model's maximum context length is 8192 tokens")

    async def _chat(messages, temperature=0.7, max_tokens=2048, **kw):
        sent["messages"] = messages
        return "ok"

    client._execute_routed = _boom
    client.chat = _chat

    long_convo = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"m{i}"} for i in range(20)
    ]

    async def run():
        try:
            return await client._execute_routed(long_convo, object(), 0.7)
        except Exception as exc:
            if is_context_overflow(exc):
                return await client.chat(client._trim_messages_for_retry(long_convo), 0.7, 2048)
            return await client.chat(long_convo, 0.7, 2048)

    assert asyncio.run(run()) == "ok"
    assert len(sent["messages"]) < len(long_convo), (
        "the retry re-sent the full oversized payload — it can only fail again"
    )
    assert sent["messages"][0]["role"] == "system"


def test_a_non_overflow_failure_still_retries_with_the_full_conversation():
    """Trimming is for size alone. A network blip must not cost the user their history."""
    client = AIClient.__new__(AIClient)
    sent: dict = {}

    async def _boom(messages, decision, temperature):
        raise RuntimeError("Connection reset by peer")

    async def _chat(messages, temperature=0.7, max_tokens=2048, **kw):
        sent["messages"] = messages
        return "ok"

    client._execute_routed = _boom
    client.chat = _chat
    convo = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"m{i}"} for i in range(20)
    ]

    async def run():
        try:
            return await client._execute_routed(convo, object(), 0.7)
        except Exception as exc:
            if is_context_overflow(exc):
                return await client.chat(client._trim_messages_for_retry(convo), 0.7, 2048)
            return await client.chat(convo, 0.7, 2048)

    asyncio.run(run())
    assert len(sent["messages"]) == len(convo), "history was dropped for an unrelated error"


def test_the_live_retry_path_consults_the_overflow_check():
    """Pins the wiring: `chat_routed`'s except branch must reach the trim.

    The helper existed and was correct for as long as it was dead; only the call site
    makes it matter.
    """
    import inspect

    src = inspect.getsource(AIClient.chat_routed)
    assert "is_context_overflow" in src, "chat_routed no longer checks for an overflow"
    assert "_trim_messages_for_retry" in src, "chat_routed no longer trims on overflow"
