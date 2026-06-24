"""Hermetic unit tests for navig.messaging.registry."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from navig.messaging.registry import (
    _messaging_cfg,
    _telegram_config,
    get_active_provider_name,
    is_provider_enabled,
    is_supported_provider_name,
    supported_provider_names,
)

# ---------------------------------------------------------------------------
# is_supported_provider_name
# ---------------------------------------------------------------------------


class TestIsSupportedProviderName:
    def test_telegram_is_supported(self):
        assert is_supported_provider_name("telegram") is True

    def test_discord_is_supported(self):
        assert is_supported_provider_name("discord") is True

    def test_none_is_supported(self):
        assert is_supported_provider_name("none") is True

    def test_unknown_is_not_supported(self):
        assert is_supported_provider_name("slack") is False

    def test_empty_is_not_supported(self):
        assert is_supported_provider_name("") is False

    def test_case_insensitive(self):
        assert is_supported_provider_name("Telegram") is True
        assert is_supported_provider_name("DISCORD") is True


# ---------------------------------------------------------------------------
# supported_provider_names
# ---------------------------------------------------------------------------


class TestSupportedProviderNames:
    def test_returns_tuple(self):
        result = supported_provider_names()
        assert isinstance(result, tuple)

    def test_contains_telegram(self):
        assert "telegram" in supported_provider_names()

    def test_contains_discord(self):
        assert "discord" in supported_provider_names()

    def test_sorted(self):
        names = list(supported_provider_names())
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# _messaging_cfg
# ---------------------------------------------------------------------------


class TestMessagingCfg:
    def test_extracts_messaging_dict(self):
        cfg = {"messaging": {"provider": "telegram"}}
        result = _messaging_cfg(cfg)
        assert result == {"provider": "telegram"}

    def test_returns_empty_when_no_messaging_key(self):
        assert _messaging_cfg({}) == {}

    def test_returns_empty_for_none(self):
        assert _messaging_cfg(None) == {}

    def test_returns_empty_when_messaging_not_dict(self):
        assert _messaging_cfg({"messaging": "telegram"}) == {}


# ---------------------------------------------------------------------------
# get_active_provider_name
# ---------------------------------------------------------------------------


class TestGetActiveProviderName:
    def test_default_is_telegram(self, monkeypatch):
        monkeypatch.delenv("NAVIG_MESSAGING_PROVIDER", raising=False)
        assert get_active_provider_name({}) == "telegram"

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("NAVIG_MESSAGING_PROVIDER", "discord")
        assert get_active_provider_name({"messaging": {"provider": "telegram"}}) == "discord"

    def test_config_overrides_default(self, monkeypatch):
        monkeypatch.delenv("NAVIG_MESSAGING_PROVIDER", raising=False)
        assert get_active_provider_name({"messaging": {"provider": "discord"}}) == "discord"

    def test_env_lowercased(self, monkeypatch):
        monkeypatch.setenv("NAVIG_MESSAGING_PROVIDER", "DISCORD")
        assert get_active_provider_name({}) == "discord"

    def test_empty_env_uses_config(self, monkeypatch):
        monkeypatch.setenv("NAVIG_MESSAGING_PROVIDER", "")
        assert get_active_provider_name({"messaging": {"provider": "sms"}}) == "sms"

    def test_none_config_returns_telegram(self, monkeypatch):
        monkeypatch.delenv("NAVIG_MESSAGING_PROVIDER", raising=False)
        assert get_active_provider_name(None) == "telegram"


# ---------------------------------------------------------------------------
# _telegram_config
# ---------------------------------------------------------------------------


class TestTelegramConfig:
    def test_token_included(self):
        with (
            patch("navig.messaging.registry.resolve_telegram_bot_token", return_value="TOKEN123"),
            patch("navig.messaging.registry.resolve_telegram_uid", return_value="UID456"),
        ):
            cfg = _telegram_config({})
        assert cfg["bot_token"] == "TOKEN123"
        assert cfg["owner_uid"] == "UID456"

    def test_no_token_is_falsy(self):
        with (
            patch("navig.messaging.registry.resolve_telegram_bot_token", return_value=None),
            patch("navig.messaging.registry.resolve_telegram_uid", return_value=None),
        ):
            cfg = _telegram_config({})
        assert not cfg["bot_token"]

    def test_default_require_auth_true(self):
        with (
            patch("navig.messaging.registry.resolve_telegram_bot_token", return_value=None),
            patch("navig.messaging.registry.resolve_telegram_uid", return_value=None),
        ):
            cfg = _telegram_config({})
        assert cfg["require_auth"] is True


# ---------------------------------------------------------------------------
# is_provider_enabled
# ---------------------------------------------------------------------------


class TestIsProviderEnabled:
    def test_telegram_enabled_when_token_present(self, monkeypatch):
        monkeypatch.delenv("NAVIG_MESSAGING_PROVIDER", raising=False)
        with (
            patch("navig.messaging.registry.resolve_telegram_bot_token", return_value="BOT:TOKEN"),
            patch("navig.messaging.registry.resolve_telegram_uid", return_value=None),
        ):
            assert is_provider_enabled("telegram", {}) is True

    def test_telegram_disabled_when_no_token(self, monkeypatch):
        monkeypatch.delenv("NAVIG_MESSAGING_PROVIDER", raising=False)
        with (
            patch("navig.messaging.registry.resolve_telegram_bot_token", return_value=None),
            patch("navig.messaging.registry.resolve_telegram_uid", return_value=None),
        ):
            assert is_provider_enabled("telegram", {}) is False

    def test_discord_not_enabled_via_legacy_provider(self, monkeypatch):
        monkeypatch.delenv("NAVIG_MESSAGING_PROVIDER", raising=False)
        # discord provider is not implemented in the legacy provider registry
        assert is_provider_enabled("discord", {}) is False

    def test_wrong_active_provider_returns_false(self, monkeypatch):
        monkeypatch.setenv("NAVIG_MESSAGING_PROVIDER", "discord")
        assert is_provider_enabled("telegram", {}) is False
