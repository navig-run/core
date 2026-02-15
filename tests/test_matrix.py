"""
Tests for NAVIG Matrix Integration — Phase 1 + Phase 2

Covers:
  - Feature toggle system
  - Vault preset existence
  - Channel registry integration
  - MatrixConfig dataclass
  - CLI command scaffold (smoke tests)
  - Matrix inbox bridge
  - Matrix notifier
  - File sharing (upload/download)
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


# ============================================================================
# Phase 2: Matrix Inbox Bridge
# ============================================================================

class TestMatrixInboxBridge:
    """Test the Matrix → NAVIG inbox bridge."""

    def test_format_inbox_md(self):
        from navig.comms.matrix_inbox import _format_inbox_md

        md = _format_inbox_md(
            sender="@alice:example.com",
            room_id="!room:example.com",
            room_name="General",
            body="Hello from Matrix!",
        )
        assert "---" in md
        assert "type: matrix_message" in md
        assert "sender: \"@alice:example.com\"" in md
        assert "room_id: \"!room:example.com\"" in md
        assert "room_name: \"General\"" in md
        assert "status: unread" in md
        assert "Hello from Matrix!" in md

    def test_sanitize_filename(self):
        from navig.comms.matrix_inbox import _sanitize_filename

        assert _sanitize_filename("Hello World!") == "hello-world"
        assert _sanitize_filename("test@#$%") == "test"
        assert _sanitize_filename("a" * 100, max_len=10) == "a" * 10
        assert _sanitize_filename("") == "matrix-message"

    def test_list_messages_empty(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        assert bridge.list_messages() == []

    @pytest.mark.asyncio
    async def test_persist_message(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        path = await bridge.on_matrix_message(
            room_id="!test:local",
            sender="@bob:local",
            body="Important update from Bob",
            room_name="TestRoom",
        )
        assert path is not None
        assert path.exists()
        assert path.suffix == ".md"

        content = path.read_text()
        assert "Important update from Bob" in content
        assert "@bob:local" in content

    @pytest.mark.asyncio
    async def test_filter_by_room(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project, filter_rooms=["!allowed:local"])

        # Should be filtered out
        result = await bridge.on_matrix_message(
            room_id="!other:local",
            sender="@bob:local",
            body="Filtered message",
        )
        assert result is None

        # Should pass
        result = await bridge.on_matrix_message(
            room_id="!allowed:local",
            sender="@bob:local",
            body="Allowed message",
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_filter_by_sender(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project, filter_senders=["@alice:local"])

        result = await bridge.on_matrix_message(
            room_id="!room:local",
            sender="@bob:local",
            body="Not from Alice",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_ignore_short_messages(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        result = await bridge.on_matrix_message(
            room_id="!room:local", sender="@x:local", body="",
        )
        assert result is None

        result = await bridge.on_matrix_message(
            room_id="!room:local", sender="@x:local", body="a",
        )
        assert result is None

    def test_mark_read(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        # Create a fake message file
        inbox = bridge.inbox_dir
        msg_file = inbox / "test-msg.md"
        msg_file.write_text("---\nstatus: unread\n---\nHello\n")

        assert bridge.mark_read("test-msg.md")
        content = msg_file.read_text()
        assert "status: read" in content
        assert "status: unread" not in content

    def test_mark_all_read(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        inbox = bridge.inbox_dir
        for i in range(3):
            (inbox / f"msg-{i}.md").write_text("---\nstatus: unread\n---\n")

        count = bridge.mark_all_read()
        assert count == 3

    def test_purge_read(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        inbox = bridge.inbox_dir
        (inbox / "read1.md").write_text("---\nstatus: read\n---\n")
        (inbox / "read2.md").write_text("---\nstatus: read\n---\n")
        (inbox / "unread1.md").write_text("---\nstatus: unread\n---\n")

        deleted = bridge.purge_read()
        assert deleted == 2
        assert (inbox / "unread1.md").exists()
        assert not (inbox / "read1.md").exists()

    def test_get_unread_count(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        inbox = bridge.inbox_dir
        (inbox / "u1.md").write_text("---\nstatus: unread\nsender: a\nroom_id: r\nroom_name: R\ncreated: now\n---\n")
        (inbox / "u2.md").write_text("---\nstatus: unread\nsender: b\nroom_id: r\nroom_name: R\ncreated: now\n---\n")
        (inbox / "r1.md").write_text("---\nstatus: read\nsender: c\nroom_id: r\nroom_name: R\ncreated: now\n---\n")

        assert bridge.get_unread_count() == 2

    def test_delete_message(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        inbox = bridge.inbox_dir
        (inbox / "del.md").write_text("---\n---\ntest\n")

        assert bridge.delete_message("del.md")
        assert not (inbox / "del.md").exists()
        assert not bridge.delete_message("nonexist.md")

    def test_parse_frontmatter(self):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        result = MatrixInboxBridge._parse_frontmatter(
            '---\ntype: matrix_message\nsender: "@alice:local"\nstatus: unread\n---\nBody'
        )
        assert result["type"] == "matrix_message"
        assert result["sender"] == "@alice:local"
        assert result["status"] == "unread"

    def test_get_inbox_bridge_factory(self, tmp_path, monkeypatch):
        from navig.comms.matrix_inbox import get_inbox_bridge

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".navig").mkdir()

        bridge = get_inbox_bridge()
        assert bridge.project_root == tmp_path


# ============================================================================
# Phase 2: Matrix Notifier
# ============================================================================

class TestMatrixNotifier:
    """Test the MatrixNotifier (ChannelNotifier implementation)."""

    def test_is_channel_notifier_subclass(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import ChannelNotifier

        assert issubclass(MatrixNotifier, ChannelNotifier)

    def test_format_for_matrix(self):
        from navig.gateway.matrix_notifier import _format_for_matrix
        from navig.gateway.notifications import Notification, NotificationPriority

        n = Notification(
            type="alert",
            title="Disk Full",
            message="Server disk at 95%",
            priority=NotificationPriority.HIGH,
        )
        text = _format_for_matrix(n)
        assert "Disk Full" in text
        assert "Server disk at 95%" in text

    @pytest.mark.asyncio
    async def test_send_critical_immediately(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import Notification, NotificationPriority

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(return_value="$evt1")
        mock_bot.send_notice = AsyncMock(return_value="$evt2")

        notifier = MatrixNotifier(mock_bot, "!default:local", priority_room_id="!priority:local")

        n = Notification(
            type="alert",
            title="Critical!",
            message="Server down",
            priority=NotificationPriority.CRITICAL,
        )
        await notifier.send(n)
        mock_bot.send_message.assert_called_once()
        args = mock_bot.send_message.call_args
        assert args[0][0] == "!priority:local"

    @pytest.mark.asyncio
    async def test_send_normal_as_notice(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import Notification, NotificationPriority

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot.send_notice = AsyncMock(return_value="$evt2")

        notifier = MatrixNotifier(mock_bot, "!default:local")

        n = Notification(
            type="briefing",
            title="Daily Report",
            message="All good",
            priority=NotificationPriority.NORMAL,
        )
        await notifier.send(n)
        mock_bot.send_notice.assert_called_once()
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_alert_convenience(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import NotificationPriority

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(return_value="$evt1")
        mock_bot.send_notice = AsyncMock(return_value="$evt2")

        notifier = MatrixNotifier(mock_bot, "!room:local")
        await notifier.send_alert("Test Alert", "Something happened")
        # HIGH priority goes through send_notice (not send_message, which is CRITICAL-only)
        mock_bot.send_notice.assert_called_once()

    @pytest.mark.asyncio
    async def test_low_priority_batched(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import Notification, NotificationPriority

        mock_bot = MagicMock()
        mock_bot.send_notice = AsyncMock(return_value="$evt")

        notifier = MatrixNotifier(mock_bot, "!room:local", batch_window_sec=999)

        n = Notification(type="reminder", title="Low1", message="msg1", priority=NotificationPriority.LOW)
        await notifier.send(n)
        # Should NOT be sent immediately
        mock_bot.send_notice.assert_not_called()
        assert len(notifier._batch_buffer) == 1

    @pytest.mark.asyncio
    async def test_flush_batch(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import Notification, NotificationPriority

        mock_bot = MagicMock()
        mock_bot.send_notice = AsyncMock(return_value="$evt")

        notifier = MatrixNotifier(mock_bot, "!room:local")
        notifier._batch_buffer = [
            Notification(type="reminder", title="A", message="aaa", priority=NotificationPriority.LOW),
            Notification(type="reminder", title="B", message="bbb", priority=NotificationPriority.LOW),
        ]

        await notifier._flush_batch()
        mock_bot.send_notice.assert_called_once()
        assert len(notifier._batch_buffer) == 0
        # Verify combined message
        call_args = mock_bot.send_notice.call_args[0]
        assert "A" in call_args[1]
        assert "B" in call_args[1]


# ============================================================================
# Phase 2: NotificationManager Matrix integration
# ============================================================================

class TestNotificationManagerMatrix:
    """Test that NotificationManager can configure Matrix."""

    def test_configure_matrix(self):
        from navig.gateway.notifications import NotificationManager

        # Reset singleton for test
        NotificationManager._instance = None

        mgr = NotificationManager()
        mock_bot = MagicMock()

        mgr.configure_matrix(mock_bot, "!alerts:local")
        assert "matrix" in mgr._channels

        # Clean up singleton
        NotificationManager._instance = None


# ============================================================================
# Phase 2: File sharing (upload/download) — unit tests
# ============================================================================

class TestFileSharing:
    """Test upload/download on NavigMatrixBot (no-client paths)."""

    @pytest.mark.asyncio
    async def test_upload_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        result = await bot.upload_file("!room:local", "/nonexistent/file.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        result = await bot.download_file("mxc://local/abc", "/tmp/test.bin")
        assert result is False

    @pytest.mark.asyncio
    async def test_upload_missing_file(self, tmp_path):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        # Even with client=None, file validation comes first
        result = await bot.upload_file("!room:local", str(tmp_path / "nope.txt"))
        assert result is None

    @pytest.mark.asyncio
    async def test_upload_empty_file(self, tmp_path):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        empty = tmp_path / "empty.txt"
        empty.write_text("")

        result = await bot.upload_file("!room:local", str(empty))
        assert result is None


# ============================================================================
# Phase 2: Inbox CLI smoke tests
# ============================================================================

class TestMatrixInboxCLI:
    """Smoke tests for navig matrix inbox commands."""

    def test_inbox_list_requires_notifications_feature(self):
        from typer.testing import CliRunner
        from navig.commands.matrix import matrix_app
        from click.exceptions import Exit

        runner = CliRunner()

        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["inbox", "list"])
            assert result.exit_code != 0

    def test_inbox_unread_requires_notifications_feature(self):
        from typer.testing import CliRunner
        from navig.commands.matrix import matrix_app

        runner = CliRunner()

        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["inbox", "unread"])
            assert result.exit_code != 0

    def test_file_upload_requires_file_sharing_feature(self):
        from typer.testing import CliRunner
        from navig.commands.matrix import matrix_app

        runner = CliRunner()

        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["file", "upload", "test.txt"])
            assert result.exit_code != 0

    def test_file_download_requires_file_sharing_feature(self):
        from typer.testing import CliRunner
        from navig.commands.matrix import matrix_app

        runner = CliRunner()

        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["file", "download", "mxc://x/y"])
            assert result.exit_code != 0
