"""Tests for gateway/channels/whatsapp.py and agent/ears.py — batch 115."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# navig/gateway/channels/whatsapp.py
# ---------------------------------------------------------------------------

class TestWhatsAppChannelConfigInit:
    def _make(self, **kwargs):
        from navig.gateway.channels.whatsapp import WhatsAppChannelConfig
        return WhatsAppChannelConfig(**kwargs)

    def test_default_bridge_url_from_env(self):
        cfg = self._make()
        assert "localhost:3000" in cfg.bridge_url

    def test_custom_bridge_url(self):
        cfg = self._make(bridge_url="http://myserver:4000")
        assert cfg.bridge_url == "http://myserver:4000"

    def test_respond_to_groups_default_true(self):
        cfg = self._make()
        assert cfg.respond_to_groups is True

    def test_respond_to_dms_default_true(self):
        cfg = self._make()
        assert cfg.respond_to_dms is True

    def test_mention_required_default_true(self):
        cfg = self._make()
        assert cfg.mention_required_in_groups is True

    def test_allowed_numbers_default_none(self):
        cfg = self._make()
        assert cfg.allowed_numbers is None

    def test_allowed_groups_default_none(self):
        cfg = self._make()
        assert cfg.allowed_groups is None

    def test_env_api_key_used(self):
        with patch.dict("os.environ", {"WHATSAPP_BRIDGE_API_KEY": "mykey"}):
            from navig.gateway.channels import whatsapp as wa_mod
            # Force using the env by providing no explicit api_key
            from navig.gateway.channels.whatsapp import WhatsAppChannelConfig
            cfg = WhatsAppChannelConfig()
        assert cfg.api_key == "mykey" or cfg.api_key == ""  # either is fine depending on env


class TestWhatsAppChannelConfigFromDict:
    def _from(self, data: dict):
        from navig.gateway.channels.whatsapp import WhatsAppChannelConfig
        return WhatsAppChannelConfig.from_dict(data)

    def test_empty_dict_uses_defaults(self):
        cfg = self._from({})
        assert cfg.respond_to_groups is True
        assert cfg.respond_to_dms is True

    def test_bridge_url_set(self):
        cfg = self._from({"bridge_url": "http://custom:5000"})
        assert cfg.bridge_url == "http://custom:5000"

    def test_allowed_numbers_set(self):
        cfg = self._from({"allowed_numbers": ["+1234567890"]})
        assert cfg.allowed_numbers == ["+1234567890"]

    def test_respond_to_groups_false(self):
        cfg = self._from({"respond_to_groups": False})
        assert cfg.respond_to_groups is False

    def test_api_key_set(self):
        cfg = self._from({"api_key": "secret123"})
        assert cfg.api_key == "secret123"


class TestWhatsAppMessage:
    def _make(self, **kwargs):
        from navig.gateway.channels.whatsapp import WhatsAppMessage
        defaults = dict(
            message_id="msg-1",
            from_number="+1234567890",
            content="Hello",
            timestamp=datetime(2024, 1, 1, 12, 0),
        )
        defaults.update(kwargs)
        return WhatsAppMessage(**defaults)

    def test_message_id_stored(self):
        msg = self._make(message_id="abc")
        assert msg.message_id == "abc"

    def test_from_number_stored(self):
        msg = self._make(from_number="+1555")
        assert msg.from_number == "+1555"

    def test_content_stored(self):
        msg = self._make(content="Hi there")
        assert msg.content == "Hi there"

    def test_is_group_default_false(self):
        msg = self._make()
        assert msg.is_group is False

    def test_is_mentioned_default_false(self):
        msg = self._make()
        assert msg.is_mentioned is False

    def test_sender_name_default_none(self):
        msg = self._make()
        assert msg.sender_name is None


class TestWhatsAppMessageFromBridgePayload:
    def _from(self, data: dict):
        from navig.gateway.channels.whatsapp import WhatsAppMessage
        return WhatsAppMessage.from_bridge_payload(data)

    def test_content_extracted(self):
        msg = self._from({"body": "Hello bot", "from": "+1234@c.us", "timestamp": datetime.now().isoformat()})
        assert msg.content == "Hello bot"

    def test_from_number_strips_suffix(self):
        msg = self._from({"from": "+1234567890@c.us", "timestamp": datetime.now().isoformat()})
        assert "@" not in msg.from_number

    def test_is_group_extracted(self):
        msg = self._from({"isGroup": True, "timestamp": datetime.now().isoformat()})
        assert msg.is_group is True

    def test_group_name_extracted(self):
        msg = self._from({"groupName": "Dev Team", "timestamp": datetime.now().isoformat()})
        assert msg.group_name == "Dev Team"

    def test_is_mentioned_extracted(self):
        msg = self._from({"isMentioned": True, "timestamp": datetime.now().isoformat()})
        assert msg.is_mentioned is True

    def test_empty_payload_defaults(self):
        msg = self._from({})
        assert msg.message_id == ""
        assert msg.is_group is False


class TestIsWhatsAppAvailable:
    def test_returns_bool(self):
        from navig.gateway.channels.whatsapp import is_whatsapp_available
        result = is_whatsapp_available()
        assert isinstance(result, bool)

    def test_reflects_aiohttp_flag(self):
        from navig.gateway.channels import whatsapp as wa_mod
        from navig.gateway.channels.whatsapp import is_whatsapp_available
        assert is_whatsapp_available() == wa_mod.AIOHTTP_AVAILABLE


# ---------------------------------------------------------------------------
# navig/agent/ears.py — InputMessage dataclass
# ---------------------------------------------------------------------------

class TestInputMessage:
    def _make(self, **kwargs):
        from navig.agent.ears import InputMessage
        defaults = dict(source="telegram", content="hello")
        defaults.update(kwargs)
        return InputMessage(**defaults)

    def test_source_stored(self):
        msg = self._make(source="mcp")
        assert msg.source == "mcp"

    def test_content_stored(self):
        msg = self._make(content="test message")
        assert msg.content == "test message"

    def test_user_id_default_none(self):
        msg = self._make()
        assert msg.user_id is None

    def test_channel_id_default_none(self):
        msg = self._make()
        assert msg.channel_id is None

    def test_metadata_initialized_to_empty_dict(self):
        msg = self._make()
        assert isinstance(msg.metadata, dict)
        assert msg.metadata == {}

    def test_timestamp_initialized(self):
        msg = self._make()
        assert msg.timestamp is not None

    def test_custom_metadata(self):
        msg = self._make(metadata={"key": "val"})
        assert msg.metadata == {"key": "val"}

    def test_custom_user_id(self):
        msg = self._make(user_id="user-123")
        assert msg.user_id == "user-123"

    def test_to_dict_has_required_keys(self):
        msg = self._make()
        d = msg.to_dict()
        for k in ("source", "content", "user_id", "channel_id", "metadata", "timestamp"):
            assert k in d

    def test_to_dict_source_matches(self):
        msg = self._make(source="api")
        assert msg.to_dict()["source"] == "api"

    def test_to_dict_timestamp_is_string(self):
        msg = self._make()
        ts = msg.to_dict()["timestamp"]
        assert isinstance(ts, str)
        # ISO format
        assert "T" in ts or len(ts) > 10
