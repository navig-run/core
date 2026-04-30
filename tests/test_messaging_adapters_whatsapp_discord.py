"""Batch 131: tests for WhatsAppCloudAdapter and DiscordMessagingAdapter."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# navig.messaging.adapters.whatsapp_cloud
# ---------------------------------------------------------------------------
from navig.messaging.adapters.whatsapp_cloud import (
    _GRAPH_API,
    WhatsAppCloudAdapter,
)
from navig.messaging.adapter import (
    DeliveryReceipt,
    DeliveryStatus,
    InboundEvent,
    ResolvedTarget,
)


class TestWhatsAppCloudAdapterInit:
    def test_default_config(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter._config == {}
        assert adapter._phone_number_id == ""
        assert adapter._access_token == ""
        assert adapter._api_version == "v18.0"
        assert adapter._session is None

    def test_custom_config(self):
        cfg = {
            "phone_number_id": "123456",
            "access_token": "tok_abc",
            "api_version": "v19.0",
        }
        adapter = WhatsAppCloudAdapter(cfg)
        assert adapter._phone_number_id == "123456"
        assert adapter._access_token == "tok_abc"
        assert adapter._api_version == "v19.0"

    def test_none_config_uses_defaults(self):
        adapter = WhatsAppCloudAdapter(None)
        assert adapter._api_version == "v18.0"


class TestWhatsAppCloudAdapterProperties:
    def setup_method(self):
        self.adapter = WhatsAppCloudAdapter()

    def test_name(self):
        assert self.adapter.name == "whatsapp"

    def test_capabilities(self):
        caps = self.adapter.capabilities
        assert "text" in caps
        assert "media" in caps
        assert "reactions" in caps

    def test_identity_mode(self):
        assert self.adapter.identity_mode == "business"

    def test_compliance(self):
        assert self.adapter.compliance == "official"


class TestWhatsAppCloudResolveTarget:
    def setup_method(self):
        self.adapter = WhatsAppCloudAdapter()

    def test_with_scheme_prefix(self):
        target = self.adapter.resolve_target("whatsapp:+33612345678")
        assert isinstance(target, ResolvedTarget)
        assert target.address == "+33612345678"
        assert target.adapter == "whatsapp_cloud"

    def test_without_scheme_prefix(self):
        target = self.adapter.resolve_target("+33612345678")
        assert target.address == "+33612345678"

    def test_strips_whitespace(self):
        target = self.adapter.resolve_target("whatsapp:  +33612345678  ")
        assert target.address == "+33612345678"

    def test_multiple_colons(self):
        # partition only splits on first colon
        target = self.adapter.resolve_target("whatsapp:+336:extra")
        assert "+336" in target.address


class TestWhatsAppCloudReceiveWebhook:
    def setup_method(self):
        self.adapter = WhatsAppCloudAdapter()

    async def test_valid_webhook_parses_correctly(self):
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "33612345678",
                            "text": {"body": "Hello!"},
                            "id": "msg001",
                        }]
                    }
                }]
            }]
        }
        event = await self.adapter.receive_webhook(payload)
        assert isinstance(event, InboundEvent)
        assert event.adapter == "whatsapp_cloud"
        assert event.sender == "33612345678"
        assert event.text == "Hello!"
        assert event.raw is payload

    async def test_empty_payload_returns_empty_sender(self):
        event = await self.adapter.receive_webhook({})
        assert event.adapter == "whatsapp_cloud"
        assert event.sender == ""
        assert event.text == ""

    async def test_missing_messages_returns_empty_sender(self):
        payload = {"entry": [{"changes": [{"value": {"messages": []}}]}]}
        event = await self.adapter.receive_webhook(payload)
        assert event.sender == ""

    async def test_raw_payload_preserved(self):
        payload = {"custom": "data"}
        event = await self.adapter.receive_webhook(payload)
        assert event.raw == payload


class TestGraphApiConstant:
    def test_graph_api_url(self):
        assert _GRAPH_API == "https://graph.facebook.com"


# ---------------------------------------------------------------------------
# navig.messaging.adapters.discord_adapter
# ---------------------------------------------------------------------------
from navig.messaging.adapters.discord_adapter import (
    DISCORD_AVAILABLE,
    DiscordMessagingAdapter,
)


class TestDiscordAdapterInit:
    def test_default_config(self):
        adapter = DiscordMessagingAdapter()
        assert adapter._config == {}
        assert adapter._bot_token == ""
        assert adapter._client is None

    def test_custom_config(self):
        adapter = DiscordMessagingAdapter({"bot_token": "Bot.TOKEN.xyz"})
        assert adapter._bot_token == "Bot.TOKEN.xyz"

    def test_none_config(self):
        adapter = DiscordMessagingAdapter(None)
        assert adapter._bot_token == ""


class TestDiscordAdapterProperties:
    def setup_method(self):
        self.adapter = DiscordMessagingAdapter()

    def test_name(self):
        assert self.adapter.name == "discord"

    def test_capabilities_includes_text(self):
        assert "text" in self.adapter.capabilities

    def test_capabilities_includes_threads(self):
        assert "threads" in self.adapter.capabilities

    def test_capabilities_includes_media(self):
        assert "media" in self.adapter.capabilities

    def test_capabilities_includes_reactions(self):
        assert "reactions" in self.adapter.capabilities

    def test_identity_mode(self):
        assert self.adapter.identity_mode == "bot"

    def test_compliance(self):
        assert self.adapter.compliance == "official"


class TestDiscordAdapterResolveTarget:
    def setup_method(self):
        self.adapter = DiscordMessagingAdapter()

    def test_with_discord_scheme(self):
        target = self.adapter.resolve_target("discord:123456789")
        assert isinstance(target, ResolvedTarget)
        assert target.address == "123456789"
        assert target.adapter == "discord"

    def test_without_scheme(self):
        target = self.adapter.resolve_target("123456789")
        assert target.address == "123456789"

    def test_strips_whitespace(self):
        target = self.adapter.resolve_target("discord:  987654321  ")
        assert target.address == "987654321"


class TestDiscordAvailability:
    def test_discord_available_is_bool(self):
        assert isinstance(DISCORD_AVAILABLE, bool)


class TestDiscordSendWithoutDiscord:
    async def test_send_fails_gracefully_when_discord_unavailable(self):
        adapter = DiscordMessagingAdapter()
        # Patch the module-level bool that send_message checks
        import navig.messaging.adapters.discord_adapter as _mod
        original = _mod.DISCORD_AVAILABLE
        _mod.DISCORD_AVAILABLE = False
        try:
            result = await adapter.send_message("12345", "hello")
            assert isinstance(result, DeliveryReceipt)
            assert result.ok is False
            assert "discord.py" in (result.error or "").lower()
        finally:
            _mod.DISCORD_AVAILABLE = original


class TestWhatsAppCloudSendMessage:
    async def test_send_success(self):
        adapter = WhatsAppCloudAdapter({
            "phone_number_id": "111",
            "access_token": "tok",
        })
        # Build a proper async context-manager mock for session.post(...)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"messages": [{"id": "wamid001"}]})

        mock_post_cm = MagicMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_cm)

        with patch.object(adapter, "_get_session", AsyncMock(return_value=mock_session)):
            result = await adapter.send_message("+33612345678", "Hello")

        assert isinstance(result, DeliveryReceipt)
        assert result.ok is True
        assert result.message_id == "wamid001"

    async def test_send_api_error_returns_failure(self):
        adapter = WhatsAppCloudAdapter()
        with patch.object(
            adapter,
            "_get_session",
            AsyncMock(side_effect=Exception("connection failed")),
        ):
            result = await adapter.send_message("+33612345678", "Hello")
        assert result.ok is False
        assert "connection failed" in (result.error or "")
