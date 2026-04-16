"""Tests for Telegram inline mode handler."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.gateway.channels.telegram_inline import (
    TelegramInlineMixin,
    _INLINE_CACHE_TTL_SECONDS,
    _INLINE_DEBOUNCE_SECONDS,
    _INLINE_MAX_RESULT_LEN,
)


# ---------------------------------------------------------------------------
# Helpers — use a real subclass so internal methods call through to real impl
# ---------------------------------------------------------------------------


class _FakeInlineChannel(TelegramInlineMixin):
    """Minimal fake channel: mocks only the infrastructure layer."""

    def __init__(self, *, authorized: bool = True, on_message_return: str = ""):
        self._api_call = AsyncMock(return_value=True)
        self._is_user_authorized = MagicMock(return_value=authorized)
        self._inline_last_call: dict = {}
        self.on_message = AsyncMock(return_value=on_message_return) if on_message_return else None
        # Default config — override per test
        self._get_inline_config = MagicMock(return_value={"inline_mode_enabled": True})


def _iq(query: str = "hello", user_id: int = 7, query_id: str = "abc123") -> dict:
    return {
        "id": query_id,
        "from": {"id": user_id},
        "query": query,
        "offset": "",
    }


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthorised_user_receives_locked_result():
    ch = _FakeInlineChannel(authorized=False)
    await TelegramInlineMixin._on_inline_query(ch, _iq())

    ch._api_call.assert_awaited_once()
    args, _ = ch._api_call.call_args
    assert args[0] == "answerInlineQuery"
    assert args[1]["results"][0]["id"] == "locked"


@pytest.mark.asyncio
async def test_inline_mode_disabled_skips_all():
    ch = _FakeInlineChannel()
    ch._get_inline_config = MagicMock(return_value={"inline_mode_enabled": False})
    await TelegramInlineMixin._on_inline_query(ch, _iq())
    ch._api_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# Empty query → hint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_query_returns_hint():
    ch = _FakeInlineChannel()
    await TelegramInlineMixin._on_inline_query(ch, _iq(query="  "))

    ch._api_call.assert_awaited_once()
    args, _ = ch._api_call.call_args
    assert args[1]["results"][0]["id"] == "hint"


# ---------------------------------------------------------------------------
# AI call path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_ai_call_answers_query():
    ch = _FakeInlineChannel(on_message_return="The answer is 42.")
    await TelegramInlineMixin._on_inline_query(ch, _iq(query="what is 6*7?"))

    ch._api_call.assert_awaited_once()
    args, _ = ch._api_call.call_args
    assert args[0] == "answerInlineQuery"
    results = args[1]["results"]
    assert len(results) == 1
    assert "42" in results[0]["input_message_content"]["message_text"]


@pytest.mark.asyncio
async def test_ai_timeout_returns_error_result():
    import asyncio

    ch = _FakeInlineChannel()

    async def _slow(*a, **kw):
        await asyncio.sleep(100)

    ch.on_message = _slow

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        await TelegramInlineMixin._on_inline_query(ch, _iq(query="slow question"))

    ch._api_call.assert_awaited_once()
    args, _ = ch._api_call.call_args
    assert args[1]["results"][0]["id"] == "error"


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


def test_build_inline_results_structure():
    ch = _FakeInlineChannel()
    results = TelegramInlineMixin._build_inline_results(ch, "my query", "my answer")
    assert len(results) == 1
    r = results[0]
    assert r["type"] == "article"
    assert "my query" in r["title"]
    assert "my answer" in r["input_message_content"]["message_text"]


def test_build_inline_results_truncates_long_answer():
    ch = _FakeInlineChannel()
    long_answer = "x" * (_INLINE_MAX_RESULT_LEN + 100)
    results = TelegramInlineMixin._build_inline_results(ch, "q", long_answer)
    description = results[0]["description"]
    assert len(description) <= 100


# ---------------------------------------------------------------------------
# Debounce
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debounce_prevents_rapid_calls():
    """A second call within the debounce window should be dropped."""
    ch = _FakeInlineChannel(on_message_return="answer")
    # Simulate that this user called just now
    ch._inline_last_call[7] = time.monotonic()

    # This call is within _INLINE_DEBOUNCE_SECONDS → should be dropped
    await TelegramInlineMixin._on_inline_query(ch, _iq(query="hello again"))

    # No API call should have been made (debounced)
    ch._api_call.assert_not_awaited()

