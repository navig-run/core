"""Tests for navig/messaging/adapters/discord_adapter.py — DiscordMessagingAdapter."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


class TestDiscordAvailabilityFlag:
    def test_discord_available_is_bool(self):
        from navig.messaging.adapters import discord_adapter
        assert isinstance(discord_adapter.DISCORD_AVAILABLE, bool)


class TestDiscordAdapterProperties:
    def _make(self):
        from navig.messaging.adapters.discord_adapter import DiscordMessagingAdapter
        with patch.object(DiscordMessagingAdapter, "__init__", return_value=None):
            adapter = DiscordMessagingAdapter.__new__(DiscordMessagingAdapter)
            adapter._token = "Bot TOKEN"
            return adapter

    def test_name_is_discord(self):
        adapter = self._make()
        assert adapter.name == "discord"

    def test_capabilities_include_text(self):
        adapter = self._make()
        assert "text" in adapter.capabilities

    def test_capabilities_include_threads(self):
        adapter = self._make()
        assert "threads" in adapter.capabilities

    def test_identity_mode_is_bot(self):
        adapter = self._make()
        assert adapter.identity_mode == "bot"

    def test_compliance_is_official(self):
        adapter = self._make()
        assert adapter.compliance == "official"


class TestDiscordResolveTarget:
    def _make(self):
        from navig.messaging.adapters.discord_adapter import DiscordMessagingAdapter
        with patch.object(DiscordMessagingAdapter, "__init__", return_value=None):
            adapter = DiscordMessagingAdapter.__new__(DiscordMessagingAdapter)
            adapter._token = "Bot TOKEN"
            return adapter

    def _get_address(self, result):
        return result.address if hasattr(result, "address") else result

    def test_resolve_strips_discord_prefix(self):
        adapter = self._make()
        result = adapter.resolve_target("discord:123456789")
        assert self._get_address(result) == "123456789"

    def test_resolve_no_prefix_unchanged(self):
        adapter = self._make()
        result = adapter.resolve_target("123456789")
        assert self._get_address(result) == "123456789"

    def test_resolve_empty_after_prefix(self):
        adapter = self._make()
        result = adapter.resolve_target("discord:")
        assert self._get_address(result) == ""
