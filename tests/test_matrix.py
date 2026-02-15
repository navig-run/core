"""
Tests for NAVIG Matrix Integration — Phase 1

Covers:
  - Feature toggle system
  - Vault preset existence
  - Channel registry integration
  - MatrixConfig dataclass
  - CLI command scaffold (smoke tests)
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ============================================================================
# Feature toggle tests
# ============================================================================

class TestMatrixFeatures:
    """Test the feature toggle system."""

    def test_defaults_exist(self):
        from navig.comms.matrix_features import MATRIX_FEATURE_DEFAULTS, FEATURE_DESCRIPTIONS

        assert "messaging" in MATRIX_FEATURE_DEFAULTS
        assert "room_management" in MATRIX_FEATURE_DEFAULTS
        assert "admin_ops" in MATRIX_FEATURE_DEFAULTS
        assert "notifications" in MATRIX_FEATURE_DEFAULTS
        assert "registration_control" in MATRIX_FEATURE_DEFAULTS
        assert "file_sharing" in MATRIX_FEATURE_DEFAULTS
        assert "e2ee" in MATRIX_FEATURE_DEFAULTS

        # Descriptions cover all defaults
        for key in MATRIX_FEATURE_DEFAULTS:
            assert key in FEATURE_DESCRIPTIONS, f"Missing description for '{key}'"

    def test_safe_defaults(self):
        """Dangerous features must be off by default."""
        from navig.comms.matrix_features import MATRIX_FEATURE_DEFAULTS

        assert MATRIX_FEATURE_DEFAULTS["admin_ops"] is False
        assert MATRIX_FEATURE_DEFAULTS["registration_control"] is False
        assert MATRIX_FEATURE_DEFAULTS["e2ee"] is False
        assert MATRIX_FEATURE_DEFAULTS["file_sharing"] is False

    def test_core_features_on(self):
        """Core messaging features must be on by default."""
        from navig.comms.matrix_features import MATRIX_FEATURE_DEFAULTS

        assert MATRIX_FEATURE_DEFAULTS["messaging"] is True
        assert MATRIX_FEATURE_DEFAULTS["room_management"] is True
        assert MATRIX_FEATURE_DEFAULTS["notifications"] is True

    def test_get_all_features_returns_all(self):
        from navig.comms.matrix_features import get_all_features, MATRIX_FEATURE_DEFAULTS

        with patch("navig.comms.matrix_features._get_matrix_features_config", return_value={}):
            features = get_all_features()
            assert set(features.keys()) == set(MATRIX_FEATURE_DEFAULTS.keys())

    def test_config_override(self):
        from navig.comms.matrix_features import is_feature_enabled

        # Override admin_ops to True in config
        with patch(
            "navig.comms.matrix_features._get_matrix_features_config",
            return_value={"admin_ops": True},
        ):
            assert is_feature_enabled("admin_ops") is True

    def test_config_fallback_to_default(self):
        from navig.comms.matrix_features import is_feature_enabled

        with patch(
            "navig.comms.matrix_features._get_matrix_features_config",
            return_value={},
        ):
            assert is_feature_enabled("messaging") is True
            assert is_feature_enabled("admin_ops") is False

    def test_unknown_feature_returns_false(self):
        from navig.comms.matrix_features import is_feature_enabled

        with patch(
            "navig.comms.matrix_features._get_matrix_features_config",
            return_value={},
        ):
            assert is_feature_enabled("nonexistent_feature") is False

    def test_require_feature_decorator_passes(self):
        """Decorated function should execute when feature is enabled."""
        from navig.comms.matrix_features import require_feature

        @require_feature("messaging")
        def dummy_func():
            return "ok"

        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=True):
            assert dummy_func() == "ok"

    def test_require_feature_decorator_blocks(self):
        """Decorated function should raise Exit when feature is disabled."""
        from navig.comms.matrix_features import require_feature
        from click.exceptions import Exit

        @require_feature("admin_ops")
        def dummy_func():
            return "ok"

        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            with pytest.raises(Exit):
                dummy_func()


# ============================================================================
# MatrixConfig tests
# ============================================================================

class TestMatrixConfig:
    """Test the MatrixConfig dataclass."""

    def test_defaults(self):
        from navig.comms.matrix import MatrixConfig

        cfg = MatrixConfig()
        assert cfg.homeserver_url == "http://localhost:6167"
        assert cfg.user_id == ""
        assert cfg.auto_join is True
        assert cfg.e2ee is False
        assert cfg.device_name == "NAVIG"

    def test_from_dict(self):
        from navig.comms.matrix import MatrixConfig

        cfg = MatrixConfig.from_dict({
            "homeserver_url": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "password": "secret",
            "extra_field": "ignored",
        })
        assert cfg.homeserver_url == "https://matrix.org"
        assert cfg.user_id == "@bot:matrix.org"
        assert cfg.password == "secret"

    def test_from_dict_empty(self):
        from navig.comms.matrix import MatrixConfig

        cfg = MatrixConfig.from_dict({})
        assert cfg.homeserver_url == "http://localhost:6167"


# ============================================================================
# Vault preset tests
# ============================================================================

class TestVaultPresets:
    """Test that Matrix vault presets are registered."""

    def test_matrix_preset_exists(self):
        from navig.vault.types import PROVIDER_PRESETS

        assert "matrix" in PROVIDER_PRESETS
        preset = PROVIDER_PRESETS["matrix"]
        assert preset["credential_type"].value == "password"

    def test_conduit_preset_exists(self):
        from navig.vault.types import PROVIDER_PRESETS

        assert "conduit" in PROVIDER_PRESETS
        preset = PROVIDER_PRESETS["conduit"]
        assert preset["credential_type"].value == "token"


# ============================================================================
# Channel registry tests
# ============================================================================

class TestChannelRegistry:
    """Test Matrix channel registration."""

    def test_matrix_in_channel_id(self):
        from navig.gateway.channels.registry import ChannelId

        assert hasattr(ChannelId, "MATRIX")
        assert ChannelId.MATRIX.value == "matrix"

    def test_matrix_in_channel_order(self):
        from navig.gateway.channels.registry import CHANNEL_ORDER, ChannelId

        assert ChannelId.MATRIX in CHANNEL_ORDER

    def test_matrix_alias(self):
        from navig.gateway.channels.registry import CHANNEL_ALIASES, ChannelId

        assert "mx" in CHANNEL_ALIASES
        assert CHANNEL_ALIASES["mx"] == ChannelId.MATRIX

    def test_matrix_meta_defined(self):
        from navig.gateway.channels.registry import DEFAULT_CHANNEL_META, ChannelId

        assert ChannelId.MATRIX in DEFAULT_CHANNEL_META
        meta = DEFAULT_CHANNEL_META[ChannelId.MATRIX]
        assert meta.label == "Matrix"
        assert meta.module_path == "navig.gateway.channels.matrix"
        assert meta.adapter_class == "MatrixChannelAdapter"

    def test_matrix_capabilities(self):
        from navig.gateway.channels.registry import (
            DEFAULT_CHANNEL_META,
            ChannelId,
            ChannelCapability,
        )

        meta = DEFAULT_CHANNEL_META[ChannelId.MATRIX]
        assert ChannelCapability.TEXT in meta.capabilities
        assert ChannelCapability.E2EE in meta.capabilities
        assert ChannelCapability.THREADS in meta.capabilities

    def test_new_capabilities_exist(self):
        """E2EE, MENTIONS, MEDIA should exist in ChannelCapability."""
        from navig.gateway.channels.registry import ChannelCapability

        assert hasattr(ChannelCapability, "E2EE")
        assert hasattr(ChannelCapability, "MENTIONS")
        assert hasattr(ChannelCapability, "MEDIA")

    def test_registry_normalize_mx_alias(self):
        from navig.gateway.channels.registry import ChannelRegistry, ChannelId

        registry = ChannelRegistry()
        registry.initialize()
        assert registry.normalize_channel_id("mx") == ChannelId.MATRIX
        assert registry.normalize_channel_id("matrix") == ChannelId.MATRIX

    def test_registry_get_matrix_channel(self):
        from navig.gateway.channels.registry import ChannelRegistry, ChannelId

        registry = ChannelRegistry()
        registry.initialize()
        meta = registry.get_channel("matrix")
        assert meta is not None
        assert meta.id == ChannelId.MATRIX


# ============================================================================
# Channel adapter tests
# ============================================================================

class TestMatrixChannelAdapter:
    """Test the gateway channel adapter."""

    def test_import(self):
        from navig.gateway.channels.matrix import MatrixChannelAdapter

        adapter = MatrixChannelAdapter()
        assert adapter.is_connected is False

    def test_adapter_with_config(self):
        from navig.gateway.channels.matrix import MatrixChannelAdapter

        adapter = MatrixChannelAdapter(config={"homeserver_url": "http://localhost:6167"})
        assert adapter._config["homeserver_url"] == "http://localhost:6167"
        assert adapter.is_connected is False


# ============================================================================
# CLI command module tests
# ============================================================================

class TestMatrixCLI:
    """Smoke tests for the CLI module."""

    def test_matrix_app_importable(self):
        from navig.commands.matrix import matrix_app

        assert matrix_app is not None
        assert matrix_app.info.name == "matrix"

    def test_room_app_importable(self):
        from navig.commands.matrix import room_app

        assert room_app is not None

    def test_admin_app_importable(self):
        from navig.commands.matrix import admin_app

        assert admin_app is not None

    def test_registration_app_importable(self):
        from navig.commands.matrix import registration_app

        assert registration_app is not None


# ============================================================================
# Admin client tests
# ============================================================================

class TestMatrixAdminClient:
    """Test the admin API client."""

    def test_import(self):
        from navig.comms.matrix_admin import MatrixAdminClient

        client = MatrixAdminClient()
        assert client.homeserver_url == "http://localhost:6167"

    def test_parse_duration(self):
        from navig.comms.matrix_admin import MatrixAdminClient

        assert MatrixAdminClient._parse_duration_ms("7d") == 7 * 86400000
        assert MatrixAdminClient._parse_duration_ms("24h") == 24 * 3600000
        assert MatrixAdminClient._parse_duration_ms("30m") == 30 * 60000
        assert MatrixAdminClient._parse_duration_ms("60s") == 60000

    def test_parse_duration_default(self):
        from navig.comms.matrix_admin import MatrixAdminClient

        # Invalid input → defaults to 7 days
        assert MatrixAdminClient._parse_duration_ms("invalid") == 7 * 86400000

    def test_get_admin_client_singleton(self):
        from navig.comms.matrix_admin import get_admin_client, _admin_client
        import navig.comms.matrix_admin as admin_mod

        # Reset singleton
        admin_mod._admin_client = None

        client = get_admin_client()
        assert client is not None

        # Second call returns same instance
        client2 = get_admin_client()
        assert client is client2

        # Cleanup
        admin_mod._admin_client = None


# ============================================================================
# NavigMatrixBot extension tests
# ============================================================================

class TestNavigMatrixBotExtensions:
    """Test the new query methods added to NavigMatrixBot."""

    def test_bot_has_get_rooms(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        assert hasattr(bot, "get_rooms")
        assert callable(bot.get_rooms)

    def test_bot_has_get_room_messages(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        assert hasattr(bot, "get_room_messages")

    def test_bot_has_get_room_members(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        assert hasattr(bot, "get_room_members")

    @pytest.mark.asyncio
    async def test_get_rooms_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        # No client initialized → returns empty
        rooms = await bot.get_rooms()
        assert rooms == []

    @pytest.mark.asyncio
    async def test_get_room_messages_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        msgs = await bot.get_room_messages("!room:local")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_get_room_members_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        members = await bot.get_room_members("!room:local")
        assert members == []
