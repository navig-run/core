"""Tests for navig.messaging.adapters.telegram_adapter."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.messaging.adapter import DeliveryStatus, InboundEvent, ResolvedTarget
from navig.messaging.adapters.telegram_adapter import TelegramMessagingAdapter


# ── fixtures ──────────────────────────────────────────────────


def _adapter(config: dict | None = None) -> TelegramMessagingAdapter:
    return TelegramMessagingAdapter(config=config)


def _adapter_with_bot() -> tuple[TelegramMessagingAdapter, MagicMock]:
    adapter = _adapter()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    adapter._bot = bot
    return adapter, bot


# ── properties ────────────────────────────────────────────────


class TestProperties:
    def test_name(self):
        assert _adapter().name == "telegram"

    def test_capabilities(self):
        caps = _adapter().capabilities
        assert "text" in caps

    def test_identity_mode(self):
        assert _adapter().identity_mode == "bot"

    def test_compliance(self):
        assert _adapter().compliance == "official"

    def test_config_defaults_to_empty_dict(self):
        a = TelegramMessagingAdapter()
        assert a._config == {}

    def test_custom_config_stored(self):
        a = TelegramMessagingAdapter(config={"token": "abc"})
        assert a._config["token"] == "abc"

    def test_bot_initially_none(self):
        assert _adapter()._bot is None


# ── send_message ─────────────────────────────────────────────


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_bot_none_returns_failure(self):
        adapter = _adapter()
        receipt = await adapter.send_message("12345", "hello")
        assert receipt.ok is False
        assert "not initialised" in receipt.error.lower()

    @pytest.mark.asyncio
    async def test_successful_send(self):
        adapter, bot = _adapter_with_bot()
        fake_msg = MagicMock(message_id=42)
        bot.send_message = AsyncMock(return_value=fake_msg)

        receipt = await adapter.send_message("12345", "hi there")
        assert receipt.ok is True
        assert receipt.message_id == "42"
        assert receipt.status == DeliveryStatus.SENT

    @pytest.mark.asyncio
    async def test_send_passes_correct_chat_id(self):
        adapter, bot = _adapter_with_bot()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        await adapter.send_message("9999", "test")
        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 9999

    @pytest.mark.asyncio
    async def test_send_uses_html_parse_mode(self):
        adapter, bot = _adapter_with_bot()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        await adapter.send_message("1", "bold text")
        call_kwargs = bot.send_message.call_args[1]
        assert call_kwargs.get("parse_mode") == "HTML"

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self):
        adapter, bot = _adapter_with_bot()
        bot.send_message = AsyncMock(side_effect=Exception("network error"))

        receipt = await adapter.send_message("1", "fail")
        assert receipt.ok is False
        assert "network error" in receipt.error

    @pytest.mark.asyncio
    async def test_failure_receipt_status(self):
        adapter, bot = _adapter_with_bot()
        bot.send_message = AsyncMock(side_effect=Exception("err"))

        receipt = await adapter.send_message("1", "x")
        assert receipt.status == DeliveryStatus.FAILED


# ── resolve_target ────────────────────────────────────────────


class TestResolveTarget:
    def test_with_prefix(self):
        adapter = _adapter()
        target = adapter.resolve_target("telegram:12345")
        assert target.adapter == "telegram"
        assert target.address == "12345"

    def test_without_prefix(self):
        adapter = _adapter()
        target = adapter.resolve_target("98765")
        assert target.address == "98765"

    def test_strips_whitespace(self):
        adapter = _adapter()
        target = adapter.resolve_target("telegram: 555 ")
        assert target.address == "555"

    def test_returns_resolved_target_type(self):
        adapter = _adapter()
        target = adapter.resolve_target("telegram:1")
        assert isinstance(target, ResolvedTarget)


# ── get_or_create_thread ──────────────────────────────────────


class TestGetOrCreateThread:
    @pytest.mark.asyncio
    async def test_calls_store(self):
        adapter = _adapter()
        fake_thread = MagicMock()
        fake_store = MagicMock()
        fake_store.get_or_create = MagicMock(return_value=fake_thread)

        with patch("navig.store.threads.get_thread_store", return_value=fake_store):
            result = await adapter.get_or_create_thread("telegram:1001")

        fake_store.get_or_create.assert_called_once_with("telegram", "1001")
        assert result is fake_thread

    @pytest.mark.asyncio
    async def test_route_without_prefix(self):
        adapter = _adapter()
        fake_store = MagicMock()
        fake_store.get_or_create = MagicMock(return_value=MagicMock())

        with patch("navig.store.threads.get_thread_store", return_value=fake_store):
            await adapter.get_or_create_thread("5555")

        fake_store.get_or_create.assert_called_once_with("telegram", "5555")


# ── receive_webhook ───────────────────────────────────────────


class TestReceiveWebhook:
    @pytest.mark.asyncio
    async def test_parses_basic_message(self):
        adapter = _adapter()
        payload = {
            "message": {
                "chat": {"id": 123},
                "from": {"id": 456},
                "text": "Hello world",
            }
        }
        event = await adapter.receive_webhook(payload)
        assert isinstance(event, InboundEvent)
        assert event.adapter == "telegram"
        assert event.remote_conversation_id == "123"
        assert event.sender == "456"
        assert event.text == "Hello world"

    @pytest.mark.asyncio
    async def test_empty_payload_no_crash(self):
        adapter = _adapter()
        event = await adapter.receive_webhook({})
        assert event.adapter == "telegram"
        assert event.text == ""

    @pytest.mark.asyncio
    async def test_raw_stored(self):
        adapter = _adapter()
        payload = {"message": {"chat": {"id": 1}, "from": {"id": 2}}}
        event = await adapter.receive_webhook(payload)
        assert event.raw == payload

    @pytest.mark.asyncio
    async def test_missing_text_defaults_to_empty(self):
        adapter = _adapter()
        payload = {"message": {"chat": {"id": 1}, "from": {"id": 2}}}
        event = await adapter.receive_webhook(payload)
        assert event.text == ""
