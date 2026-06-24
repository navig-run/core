"""Unit tests for messaging/adapters/discord_adapter.py and whatsapp_cloud.py."""
from __future__ import annotations

import pytest

from navig.messaging.adapters.discord_adapter import (
    DISCORD_AVAILABLE,
    DiscordMessagingAdapter,
)
from navig.messaging.adapters.whatsapp_cloud import (
    WhatsAppCloudAdapter,
    _GRAPH_API,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_graph_api_url(self):
        assert "facebook.com" in _GRAPH_API

    def test_discord_available_is_bool(self):
        assert isinstance(DISCORD_AVAILABLE, bool)


# ---------------------------------------------------------------------------
# DiscordMessagingAdapter
# ---------------------------------------------------------------------------

class TestDiscordMessagingAdapter:
    def test_name_property(self):
        adapter = DiscordMessagingAdapter()
        assert adapter.name == "discord"

    def test_identity_mode(self):
        adapter = DiscordMessagingAdapter()
        assert adapter.identity_mode == "bot"

    def test_compliance(self):
        adapter = DiscordMessagingAdapter()
        assert adapter.compliance == "official"

    def test_capabilities_is_list(self):
        adapter = DiscordMessagingAdapter()
        assert isinstance(adapter.capabilities, list)

    def test_capabilities_includes_text(self):
        adapter = DiscordMessagingAdapter()
        assert "text" in adapter.capabilities

    def test_capabilities_includes_media(self):
        adapter = DiscordMessagingAdapter()
        assert "media" in adapter.capabilities

    def test_init_no_args(self):
        adapter = DiscordMessagingAdapter()
        assert adapter._bot_token == ""

    def test_init_with_config(self):
        adapter = DiscordMessagingAdapter({"bot_token": "my-token"})
        assert adapter._bot_token == "my-token"

    def test_init_empty_config(self):
        adapter = DiscordMessagingAdapter({})
        assert adapter._client is None

    def test_config_stored(self):
        cfg = {"bot_token": "tok", "extra": 1}
        adapter = DiscordMessagingAdapter(cfg)
        assert adapter._config == cfg


# ---------------------------------------------------------------------------
# WhatsAppCloudAdapter
# ---------------------------------------------------------------------------

class TestWhatsAppCloudAdapter:
    def test_name_property(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter.name == "whatsapp"

    def test_identity_mode(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter.identity_mode == "business"

    def test_compliance(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter.compliance == "official"

    def test_capabilities_is_list(self):
        adapter = WhatsAppCloudAdapter()
        assert isinstance(adapter.capabilities, list)

    def test_capabilities_includes_text(self):
        adapter = WhatsAppCloudAdapter()
        assert "text" in adapter.capabilities

    def test_init_no_args(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter._phone_number_id == ""
        assert adapter._access_token == ""

    def test_init_with_config(self):
        adapter = WhatsAppCloudAdapter({
            "phone_number_id": "123",
            "access_token": "abc",
            "api_version": "v20.0",
        })
        assert adapter._phone_number_id == "123"
        assert adapter._access_token == "abc"
        assert adapter._api_version == "v20.0"

    def test_default_api_version(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter._api_version == "v18.0"

    def test_session_initially_none(self):
        adapter = WhatsAppCloudAdapter()
        assert adapter._session is None

    def test_none_config_normalised_to_empty(self):
        adapter = WhatsAppCloudAdapter(None)
        assert adapter._config == {}
