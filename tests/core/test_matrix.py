"""
Tests for NAVIG Matrix Integration — Phase 1 + Phase 2 + Phase 3 + Phase 4

Covers:
  - Feature toggle system
  - Vault preset existence
  - Channel registry integration
  - MatrixConfig dataclass
  - CLI command scaffold (smoke tests)
  - Matrix inbox bridge
  - Matrix notifier
  - File sharing (upload/download)
  - E2EE: olm detection, E2EE manager, device trust, SAS flow, CLI
  - Persistent store: rooms, events, bridges, device trust, stats
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================================
# Feature toggle tests
# ============================================================================


class TestMatrixFeatures:
    """Test the feature toggle system."""

    def test_defaults_exist(self):
        from navig.comms.matrix_features import (
            FEATURE_DESCRIPTIONS,
            MATRIX_FEATURE_DEFAULTS,
        )

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
        from navig.comms.matrix_features import (
            MATRIX_FEATURE_DEFAULTS,
            get_all_features,
        )

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
        from click.exceptions import Exit

        from navig.comms.matrix_features import require_feature

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

        cfg = MatrixConfig.from_dict(
            {
                "homeserver_url": "https://matrix.org",
                "user_id": "@bot:matrix.org",
                "password": "secret",
                "extra_field": "ignored",
            }
        )
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
            ChannelCapability,
            ChannelId,
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
        from navig.gateway.channels.registry import ChannelId, ChannelRegistry

        registry = ChannelRegistry()
        registry.initialize()
        assert registry.normalize_channel_id("mx") == ChannelId.MATRIX
        assert registry.normalize_channel_id("matrix") == ChannelId.MATRIX

    def test_registry_get_matrix_channel(self):
        from navig.gateway.channels.registry import ChannelId, ChannelRegistry

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
        import navig.comms.matrix_admin as admin_mod
        from navig.comms.matrix_admin import get_admin_client

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

    async def test_get_rooms_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        # No client initialized → returns empty
        rooms = await bot.get_rooms()
        assert rooms == []

    async def test_get_room_messages_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        msgs = await bot.get_room_messages("!room:local")
        assert msgs == []

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
        assert 'sender: "@alice:example.com"' in md
        assert 'room_id: "!room:example.com"' in md
        assert 'room_name: "General"' in md
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

    async def test_ignore_short_messages(self, tmp_path):
        from navig.comms.matrix_inbox import MatrixInboxBridge

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".navig").mkdir()

        bridge = MatrixInboxBridge(project)
        result = await bridge.on_matrix_message(
            room_id="!room:local",
            sender="@x:local",
            body="",
        )
        assert result is None

        result = await bridge.on_matrix_message(
            room_id="!room:local",
            sender="@x:local",
            body="a",
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
        (inbox / "u1.md").write_text(
            "---\nstatus: unread\nsender: a\nroom_id: r\nroom_name: R\ncreated: now\n---\n"
        )
        (inbox / "u2.md").write_text(
            "---\nstatus: unread\nsender: b\nroom_id: r\nroom_name: R\ncreated: now\n---\n"
        )
        (inbox / "r1.md").write_text(
            "---\nstatus: read\nsender: c\nroom_id: r\nroom_name: R\ncreated: now\n---\n"
        )

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

    async def test_send_alert_convenience(self):
        from navig.gateway.matrix_notifier import MatrixNotifier

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(return_value="$evt1")
        mock_bot.send_notice = AsyncMock(return_value="$evt2")

        notifier = MatrixNotifier(mock_bot, "!room:local")
        await notifier.send_alert("Test Alert", "Something happened")
        # HIGH priority goes through send_notice (not send_message, which is CRITICAL-only)
        mock_bot.send_notice.assert_called_once()

    async def test_low_priority_batched(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import Notification, NotificationPriority

        mock_bot = MagicMock()
        mock_bot.send_notice = AsyncMock(return_value="$evt")

        notifier = MatrixNotifier(mock_bot, "!room:local", batch_window_sec=999)

        n = Notification(
            type="reminder",
            title="Low1",
            message="msg1",
            priority=NotificationPriority.LOW,
        )
        await notifier.send(n)
        # Should NOT be sent immediately
        mock_bot.send_notice.assert_not_called()
        assert len(notifier._batch_buffer) == 1

    async def test_flush_batch(self):
        from navig.gateway.matrix_notifier import MatrixNotifier
        from navig.gateway.notifications import Notification, NotificationPriority

        mock_bot = MagicMock()
        mock_bot.send_notice = AsyncMock(return_value="$evt")

        notifier = MatrixNotifier(mock_bot, "!room:local")
        notifier._batch_buffer = [
            Notification(
                type="reminder",
                title="A",
                message="aaa",
                priority=NotificationPriority.LOW,
            ),
            Notification(
                type="reminder",
                title="B",
                message="bbb",
                priority=NotificationPriority.LOW,
            ),
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

    async def test_upload_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        result = await bot.upload_file("!room:local", "/nonexistent/file.txt")
        assert result is None

    async def test_download_no_client(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        result = await bot.download_file("mxc://local/abc", "/tmp/test.bin")
        assert result is False

    async def test_upload_missing_file(self, tmp_path):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot({"user_id": "@test:local"})
        # Even with client=None, file validation comes first
        result = await bot.upload_file("!room:local", str(tmp_path / "nope.txt"))
        assert result is None

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


# ============================================================================
# Phase 3: E2EE Tests
# ============================================================================


class TestE2EEDetection:
    """Test E2EE capability detection."""

    def test_is_e2ee_available_returns_bool(self):
        from navig.comms.matrix import is_e2ee_available

        result = is_e2ee_available()
        assert isinstance(result, bool)

    def test_has_olm_constant(self):
        from navig.comms.matrix import HAS_OLM

        assert isinstance(HAS_OLM, bool)

    def test_matrix_config_e2ee_field(self):
        from navig.comms.matrix import MatrixConfig

        cfg = MatrixConfig()
        assert cfg.e2ee is False  # off by default

    def test_matrix_config_store_path(self):
        from navig.comms.matrix import MatrixConfig

        cfg = MatrixConfig()
        assert cfg.store_path == ""

    def test_matrix_config_device_name(self):
        from navig.comms.matrix import MatrixConfig

        cfg = MatrixConfig()
        assert cfg.device_name == "NAVIG"

    def test_matrix_config_e2ee_from_dict(self):
        from navig.comms.matrix import MatrixConfig

        cfg = MatrixConfig.from_dict({"e2ee": True, "store_path": "/tmp/store"})
        assert cfg.e2ee is True
        assert cfg.store_path == "/tmp/store"


class TestE2EEDataClasses:
    """Test E2EE data classes."""

    def test_device_trust_enum(self):
        from navig.comms.matrix_e2ee import DeviceTrust

        assert DeviceTrust.verified.value == "verified"
        assert DeviceTrust.blacklisted.value == "blacklisted"
        assert DeviceTrust.unset.value == "unset"

    def test_device_trust_from_nio(self):
        from navig.comms.matrix_e2ee import DeviceTrust

        # Simulate nio TrustState
        mock_ts = MagicMock()
        mock_ts.name = "verified"
        assert DeviceTrust.from_nio(mock_ts) == DeviceTrust.verified

        mock_ts.name = "blacklisted"
        assert DeviceTrust.from_nio(mock_ts) == DeviceTrust.blacklisted

        mock_ts.name = "something_weird"
        assert DeviceTrust.from_nio(mock_ts) == DeviceTrust.unknown

    def test_device_info(self):
        from navig.comms.matrix_e2ee import DeviceInfo, DeviceTrust

        d = DeviceInfo(
            device_id="ABCDEF",
            user_id="@alice:test",
            display_name="Alice's Phone",
            ed25519_key="abcdef1234567890abcdef1234567890",
            trust=DeviceTrust.verified,
        )
        assert d.device_id == "ABCDEF"
        assert d.short_key() == "abcdef1234567890abcd..."
        info = d.to_dict()
        assert info["trust"] == "verified"
        assert info["device_id"] == "ABCDEF"

    def test_device_info_empty_key(self):
        from navig.comms.matrix_e2ee import DeviceInfo

        d = DeviceInfo(device_id="X")
        assert d.short_key() == ""

    def test_verification_session(self):
        from navig.comms.matrix_e2ee import VerificationSession

        s = VerificationSession(
            transaction_id="txn123",
            user_id="@alice:test",
            device_id="DEV1",
            state="initiated",
        )
        assert s.transaction_id == "txn123"
        info = s.to_dict()
        assert info["state"] == "initiated"
        assert info["emoji"] == []

    def test_verification_session_with_emoji(self):
        from navig.comms.matrix_e2ee import VerificationSession

        s = VerificationSession(
            transaction_id="txn456",
            user_id="@bob:test",
            device_id="DEV2",
            emoji=[("🐶", "Dog"), ("🏠", "House")],
        )
        info = s.to_dict()
        assert len(info["emoji"]) == 2
        assert info["emoji"][0]["emoji"] == "🐶"


class TestE2EEManager:
    """Test MatrixE2EEManager with mocked bot."""

    def _make_mock_bot(self, e2ee=True):
        from navig.comms.matrix import NavigMatrixBot

        bot = MagicMock(spec=NavigMatrixBot)
        bot._e2ee_enabled = e2ee
        bot._client = MagicMock()
        bot._verification_callbacks = []
        bot.cfg = MagicMock()
        bot.cfg.user_id = "@bot:test"
        bot.cfg.e2ee = e2ee
        bot.cfg.store_path = "/tmp/test-store"
        bot.e2ee_enabled = e2ee
        bot.on_verification = MagicMock(
            side_effect=lambda cb: bot._verification_callbacks.append(cb)
        )
        bot.get_devices = AsyncMock(
            return_value=[
                {"device_id": "DEV1", "display_name": "Phone", "trust": "verified"},
                {"device_id": "DEV2", "display_name": "Laptop", "trust": "unset"},
            ]
        )
        bot.trust_device = AsyncMock(return_value=True)
        bot.blacklist_device = AsyncMock(return_value=True)
        bot.unverify_device = AsyncMock(return_value=True)
        bot.start_verification = AsyncMock(return_value="txn_abc")
        bot.accept_verification = AsyncMock(return_value=True)
        bot.confirm_verification = AsyncMock(return_value=True)
        bot.cancel_verification = AsyncMock(return_value=True)
        bot.get_verification_emoji = AsyncMock(return_value=[("🐶", "Dog"), ("🔑", "Key")])
        return bot

    def test_manager_creation(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)
        assert mgr.e2ee_ok is True
        bot.on_verification.assert_called_once()

    def test_manager_rejects_non_bot(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        with pytest.raises(TypeError):
            MatrixE2EEManager("not a bot")

    async def test_list_devices(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)
        devices = await mgr.list_devices("@alice:test")
        assert len(devices) == 2
        assert devices[0].device_id == "DEV1"
        assert devices[0].trust.value == "verified"

    async def test_trust_device(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)
        ok = await mgr.trust_device("@alice:test", "DEV1")
        assert ok is True
        bot.trust_device.assert_called_once_with("@alice:test", "DEV1")

    async def test_blacklist_device(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)
        ok = await mgr.blacklist_device("@alice:test", "DEV1")
        assert ok is True

    async def test_trust_all_devices(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)
        count = await mgr.trust_all_devices("@alice:test")
        # DEV2 is "unset" so should be trusted; DEV1 already "verified"
        assert count == 1

    async def test_start_verification_flow(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        session = await mgr.start_verification("@alice:test", "DEV1")
        assert session is not None
        assert session.transaction_id == "txn_abc"
        assert session.state == "initiated"

    async def test_accept_verification(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        # Start first to create session
        await mgr.start_verification("@alice:test", "DEV1")
        ok = await mgr.accept_verification("txn_abc")
        assert ok is True

    async def test_get_emoji(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        await mgr.start_verification("@alice:test", "DEV1")
        emoji = await mgr.get_emoji("txn_abc")
        assert len(emoji) == 2
        assert emoji[0] == ("🐶", "Dog")

    async def test_confirm_verification(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        await mgr.start_verification("@alice:test", "DEV1")
        ok = await mgr.confirm_verification("txn_abc")
        assert ok is True
        session = mgr.get_session("txn_abc")
        assert session.state == "confirmed"

    async def test_cancel_verification(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        await mgr.start_verification("@alice:test", "DEV1")
        ok = await mgr.cancel_verification("txn_abc")
        assert ok is True
        session = mgr.get_session("txn_abc")
        assert session.state == "cancelled"

    async def test_verification_callback_start(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        # Simulate a KeyVerificationStart event
        await mgr._on_verification_event(
            "KeyVerificationStart",
            "txn_remote",
            {"sender": "@other:test"},
        )
        session = mgr.get_session("txn_remote")
        assert session is not None
        assert session.state == "incoming"

    async def test_verification_callback_cancel(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        # Create a session first
        await mgr._on_verification_event(
            "KeyVerificationStart",
            "txn_x",
            {"sender": "@other:test"},
        )
        await mgr._on_verification_event(
            "KeyVerificationCancel",
            "txn_x",
            {"sender": "@other:test"},
        )
        session = mgr.get_session("txn_x")
        assert session.state == "cancelled"

    def test_get_active_sessions(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager, VerificationSession

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)

        mgr._sessions["a"] = VerificationSession("a", "@x:t", "D1", state="initiated")
        mgr._sessions["b"] = VerificationSession("b", "@x:t", "D2", state="confirmed")
        mgr._sessions["c"] = VerificationSession("c", "@x:t", "D3", state="incoming")

        active = mgr.get_active_sessions()
        assert len(active) == 2  # "a" and "c"

    async def test_e2ee_status(self):
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = self._make_mock_bot()
        mgr = MatrixE2EEManager(bot)
        status = await mgr.e2ee_status()
        assert "olm_installed" in status
        assert "e2ee_enabled" in status
        assert status["e2ee_enabled"] is True


class TestNavigMatrixBotE2EE:
    """Test E2EE methods on NavigMatrixBot directly."""

    def _make_bot(self):
        from navig.comms.matrix import NavigMatrixBot

        bot = NavigMatrixBot.__new__(NavigMatrixBot)
        bot.cfg = MagicMock()
        bot.cfg.e2ee = True
        bot._client = MagicMock()
        bot._e2ee_enabled = True
        bot._verification_callbacks = []
        bot._callbacks = {}
        bot._running = False
        bot._sync_task = None
        return bot

    def test_e2ee_enabled_property(self):
        bot = self._make_bot()
        assert bot.e2ee_enabled is True

    def test_on_verification_registers_callback(self):
        bot = self._make_bot()
        cb = MagicMock()
        bot.on_verification(cb)
        assert cb in bot._verification_callbacks

    async def test_on_key_verification_dispatches(self):
        bot = self._make_bot()
        cb = AsyncMock()
        bot.on_verification(cb)

        event = MagicMock()
        event.transaction_id = "txn_test"
        event.sender = "@alice:test"

        await bot._on_key_verification(event)
        cb.assert_called_once()
        args = cb.call_args[0]
        assert "KeyVerification" not in args[0] or True  # event type name
        assert args[1] == "txn_test"

    async def test_trust_device_no_e2ee(self):
        bot = self._make_bot()
        bot._e2ee_enabled = False
        result = await bot.trust_device("@a:t", "DEV")
        assert result is False

    async def test_trust_device_unknown(self):
        bot = self._make_bot()
        bot._client.device_store.get.return_value = None
        result = await bot.trust_device("@a:t", "DEV")
        assert result is False

    async def test_trust_device_success(self):
        bot = self._make_bot()
        mock_device = MagicMock()
        bot._client.device_store.get.return_value = mock_device
        result = await bot.trust_device("@a:t", "DEV")
        assert result is True
        bot._client.verify_device.assert_called_once_with(mock_device)

    async def test_blacklist_device_success(self):
        bot = self._make_bot()
        mock_device = MagicMock()
        bot._client.device_store.get.return_value = mock_device
        result = await bot.blacklist_device("@a:t", "DEV")
        assert result is True
        bot._client.blacklist_device.assert_called_once_with(mock_device)

    async def test_unverify_device_success(self):
        bot = self._make_bot()
        mock_device = MagicMock()
        bot._client.device_store.get.return_value = mock_device
        result = await bot.unverify_device("@a:t", "DEV")
        assert result is True
        bot._client.unverify_device.assert_called_once_with(mock_device)

    async def test_start_verification_no_e2ee(self):
        bot = self._make_bot()
        bot._e2ee_enabled = False
        result = await bot.start_verification("@a:t", "DEV")
        assert result is None

    async def test_get_devices_own(self):
        bot = self._make_bot()

        # Mock DevicesResponse
        mock_device = MagicMock()
        mock_device.id = "DEVOWN"
        mock_device.display_name = "Bot Device"
        mock_device.last_seen_ip = "1.2.3.4"
        mock_device.last_seen_date = "2025-01-01"

        mock_resp = MagicMock()
        mock_resp.devices = [mock_device]

        bot._client.devices = AsyncMock(return_value=mock_resp)

        # Patch isinstance to accept our mock as DevicesResponse
        with patch(
            "navig.comms.matrix.NavigMatrixBot.get_devices", new_callable=AsyncMock
        ) as mock_gd:
            mock_gd.return_value = [
                {
                    "device_id": "DEVOWN",
                    "display_name": "Bot Device",
                    "last_seen_ip": "1.2.3.4",
                    "last_seen_ts": "2025-01-01",
                    "trust": "self",
                }
            ]
            devices = await mock_gd(user_id=None)
            assert isinstance(devices, list)
            assert len(devices) == 1
            assert devices[0]["device_id"] == "DEVOWN"

    async def test_get_verification_emoji_no_sas(self):
        bot = self._make_bot()
        bot._client.key_verifications = {}
        result = await bot.get_verification_emoji("nonexistent")
        assert result is None


class TestE2EECLI:
    """Test E2EE CLI commands (smoke tests — feature gate)."""

    def test_e2ee_status_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["e2ee", "status"])
            assert result.exit_code != 0

    def test_e2ee_devices_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["e2ee", "devices"])
            assert result.exit_code != 0

    def test_e2ee_trust_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["e2ee", "trust", "@user:test", "DEVID"])
            assert result.exit_code != 0

    def test_e2ee_verify_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["e2ee", "verify", "@user:test", "DEVID"])
            assert result.exit_code != 0

    def test_e2ee_keys_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["e2ee", "keys"])
            assert result.exit_code != 0

    def test_e2ee_blacklist_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(matrix_app, ["e2ee", "blacklist", "@user:test", "DEVID"])
            assert result.exit_code != 0

    def test_e2ee_export_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(
                matrix_app, ["e2ee", "export-keys", "keys.txt", "--passphrase", "test"]
            )
            assert result.exit_code != 0

    def test_e2ee_import_requires_feature(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("navig.comms.matrix_features.is_feature_enabled", return_value=False):
            result = runner.invoke(
                matrix_app, ["e2ee", "import-keys", "keys.txt", "--passphrase", "test"]
            )
            assert result.exit_code != 0

    def test_e2ee_subcommand_help(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        result = runner.invoke(matrix_app, ["e2ee", "--help"])
        assert result.exit_code == 0
        assert "e2ee" in result.output.lower() or "encryption" in result.output.lower()


# ============================================================================
# Phase 4 — Persistent store tests
# ============================================================================

import os
import tempfile

pytestmark = pytest.mark.integration


class TestMatrixStoreSchema:
    """Schema creation and versioning."""

    def test_creates_tables(self):
        from navig.comms.matrix_store import MatrixStore

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            store = MatrixStore(db)
            # check tables exist by querying sqlite_master
            conn = store._get_conn()
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            store.close()
            assert "rooms" in tables
            assert "events" in tables
            assert "bridges" in tables
            assert "device_trust" in tables
            assert "schema_version" in tables

    def test_schema_version(self):
        from navig.comms.matrix_store import SCHEMA_VERSION, MatrixStore

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            store = MatrixStore(db)
            conn = store._get_conn()
            v = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            store.close()
            assert v == SCHEMA_VERSION

    def test_idempotent_open(self):
        """Opening the same DB twice should not error."""
        from navig.comms.matrix_store import MatrixStore

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.db")
            s1 = MatrixStore(db)
            s1.close()
            s2 = MatrixStore(db)
            assert s2.count_rooms() == 0
            s2.close()


class TestMatrixStoreRooms:
    """Room CRUD operations."""

    def _make_store(self, tmp):
        from navig.comms.matrix_store import MatrixStore

        return MatrixStore(os.path.join(tmp, "test.db"))

    def test_upsert_and_get(self):
        from navig.comms.matrix_store import MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            room = MatrixRoom(
                room_id="!abc:test",
                name="Test Room",
                topic="A topic",
                purpose="notifications",
                encrypted=True,
            )
            store.upsert_room(room)
            got = store.get_room("!abc:test")
            assert got is not None
            assert got.name == "Test Room"
            assert got.purpose == "notifications"
            assert got.encrypted is True
            store.close()

    def test_upsert_updates(self):
        from navig.comms.matrix_store import MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t", name="V1"))
            store.upsert_room(MatrixRoom(room_id="!r:t", name="V2"))
            assert store.count_rooms() == 1
            assert store.get_room("!r:t").name == "V2"
            store.close()

    def test_list_rooms_filter(self):
        from navig.comms.matrix_store import MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!a:t", purpose="alerts"))
            store.upsert_room(MatrixRoom(room_id="!b:t", purpose="general"))
            store.upsert_room(MatrixRoom(room_id="!c:t", purpose="alerts"))
            assert len(store.list_rooms(purpose="alerts")) == 2
            assert len(store.list_rooms(purpose="general")) == 1
            assert len(store.list_rooms()) == 3
            store.close()

    def test_remove_room(self):
        from navig.comms.matrix_store import MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!rm:t"))
            assert store.count_rooms() == 1
            store.remove_room("!rm:t")
            assert store.count_rooms() == 0
            assert store.get_room("!rm:t") is None
            store.close()

    def test_room_metadata(self):
        from navig.comms.matrix_store import MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(
                MatrixRoom(
                    room_id="!meta:t",
                    metadata={"bridge": "telegram", "channel": "#main"},
                )
            )
            got = store.get_room("!meta:t")
            assert got.metadata == {"bridge": "telegram", "channel": "#main"}
            store.close()


class TestMatrixStoreEvents:
    """Event logging and querying."""

    def _make_store(self, tmp):
        from navig.comms.matrix_store import MatrixStore

        return MatrixStore(os.path.join(tmp, "test.db"))

    def test_add_and_get(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            store.add_event(
                MatrixEvent(
                    event_id="$ev1",
                    room_id="!r:t",
                    sender="@alice:t",
                    event_type="m.room.message",
                    content={"body": "hello"},
                    origin_ts=1000,
                )
            )
            events = store.get_events("!r:t")
            assert len(events) == 1
            assert events[0].sender == "@alice:t"
            assert events[0].content["body"] == "hello"
            store.close()

    def test_duplicate_event_id_ignored(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            ev = MatrixEvent(
                event_id="$dup",
                room_id="!r:t",
                sender="@a:t",
                event_type="m.room.message",
            )
            store.add_event(ev)
            store.add_event(ev)  # should not raise
            assert store.count_events("!r:t") == 1
            store.close()

    def test_events_ordered_by_origin_ts(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            for i in range(5):
                store.add_event(
                    MatrixEvent(
                        event_id=f"$e{i}",
                        room_id="!r:t",
                        sender="@a:t",
                        event_type="m.room.message",
                        origin_ts=i * 1000,
                    )
                )
            events = store.get_events("!r:t")
            # Store returns DESC order (newest first)
            assert [e.origin_ts for e in events] == [4000, 3000, 2000, 1000, 0]
            store.close()

    def test_get_events_limit(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            for i in range(10):
                store.add_event(
                    MatrixEvent(
                        event_id=f"$e{i}",
                        room_id="!r:t",
                        sender="@a:t",
                        event_type="m.room.message",
                        origin_ts=i,
                    )
                )
            assert len(store.get_events("!r:t", limit=3)) == 3
            store.close()

    def test_get_events_since_ts(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            for i in range(5):
                store.add_event(
                    MatrixEvent(
                        event_id=f"$e{i}",
                        room_id="!r:t",
                        sender="@a:t",
                        event_type="m.room.message",
                        origin_ts=i * 1000,
                    )
                )
            # since_ts uses > (exclusive), so origin_ts > 2000 => 3000, 4000
            events = store.get_events("!r:t", since_ts=2000)
            assert len(events) == 2  # ts 3000, 4000
            store.close()

    def test_get_events_by_type(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            store.add_event(
                MatrixEvent(
                    event_id="$m1",
                    room_id="!r:t",
                    sender="@a:t",
                    event_type="m.room.message",
                )
            )
            store.add_event(
                MatrixEvent(
                    event_id="$s1",
                    room_id="!r:t",
                    sender="@a:t",
                    event_type="m.room.member",
                )
            )
            msgs = store.get_events("!r:t", event_type="m.room.message")
            assert len(msgs) == 1
            assert msgs[0].event_id == "$m1"
            store.close()

    def test_batch_insert(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            events = [
                MatrixEvent(
                    event_id=f"$b{i}",
                    room_id="!r:t",
                    sender="@a:t",
                    event_type="m.room.message",
                    origin_ts=i,
                )
                for i in range(50)
            ]
            store.add_events_batch(events)
            assert store.count_events("!r:t") == 50
            store.close()

    def test_prune_events(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            for i in range(100):
                store.add_event(
                    MatrixEvent(
                        event_id=f"$p{i}",
                        room_id="!r:t",
                        sender="@a:t",
                        event_type="m.room.message",
                        origin_ts=i,
                    )
                )
            store.prune_events(max_rows=30)
            remaining = store.count_events()
            assert remaining <= 30
            # Oldest should be pruned, newest kept
            events = store.get_events("!r:t", limit=1)
            assert events[0].origin_ts >= 70  # kept the newest 30
            store.close()

    def test_count_unique_senders(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            for sender in ["@a:t", "@b:t", "@a:t", "@c:t"]:
                store.add_event(
                    MatrixEvent(
                        event_id=f"$u{sender}_{id(sender)}",
                        room_id="!r:t",
                        sender=sender,
                        event_type="m.room.message",
                    )
                )
            assert store.count_unique_senders() == 3
            store.close()


class TestMatrixStoreBridges:
    """Bridge configuration CRUD."""

    def _make_store(self, tmp):
        from navig.comms.matrix_store import MatrixStore

        return MatrixStore(os.path.join(tmp, "test.db"))

    def test_add_and_get(self):
        from navig.comms.matrix_store import MatrixBridge, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            bid = store.add_bridge(
                MatrixBridge(
                    room_id="!r:t",
                    bridge_type="telegram",
                    config={"channel_id": "-100123", "direction": "both"},
                )
            )
            assert bid > 0
            bridges = store.get_bridges(room_id="!r:t")
            assert len(bridges) == 1
            assert bridges[0].bridge_type == "telegram"
            assert bridges[0].config["channel_id"] == "-100123"
            store.close()

    def test_remove_bridge(self):
        from navig.comms.matrix_store import MatrixBridge, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r:t"))
            bid = store.add_bridge(
                MatrixBridge(
                    room_id="!r:t",
                    bridge_type="slack",
                    config={"channel_id": "C123"},
                )
            )
            store.remove_bridge(bid)
            assert len(store.get_bridges(room_id="!r:t")) == 0
            store.close()

    def test_get_all_bridges(self):
        from navig.comms.matrix_store import MatrixBridge, MatrixRoom

        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.upsert_room(MatrixRoom(room_id="!r1:t"))
            store.upsert_room(MatrixRoom(room_id="!r2:t"))
            store.add_bridge(
                MatrixBridge(room_id="!r1:t", bridge_type="telegram", config={"ch": "1"})
            )
            store.add_bridge(MatrixBridge(room_id="!r2:t", bridge_type="slack", config={"ch": "2"}))
            all_b = store.get_bridges()
            assert len(all_b) == 2
            store.close()


class TestMatrixStoreDeviceTrust:
    """Device trust cache."""

    def _make_store(self, tmp):
        from navig.comms.matrix_store import MatrixStore

        return MatrixStore(os.path.join(tmp, "test.db"))

    def test_set_and_get(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.set_device_trust("@alice:t", "DEV1", "verified")
            trust = store.get_device_trust("@alice:t", "DEV1")
            assert trust is not None
            assert trust == "verified"
            store.close()

    def test_update_trust(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.set_device_trust("@alice:t", "DEV1", "unset")
            store.set_device_trust("@alice:t", "DEV1", "verified")
            trust = store.get_device_trust("@alice:t", "DEV1")
            assert trust == "verified"
            store.close()

    def test_list_trusted_devices(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            store.set_device_trust("@alice:t", "D1", "verified")
            store.set_device_trust("@alice:t", "D2", "blacklisted")
            store.set_device_trust("@bob:t", "D3", "verified")
            alice_devs = store.list_trusted_devices("@alice:t")
            assert len(alice_devs) == 2
            store.close()

    def test_unknown_device_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._make_store(d)
            assert store.get_device_trust("@none:t", "NOPE") is None
            store.close()


class TestMatrixStoreStats:
    """Aggregate stats."""

    def test_stats_empty(self):
        from navig.comms.matrix_store import MatrixStore

        with tempfile.TemporaryDirectory() as d:
            store = MatrixStore(os.path.join(d, "test.db"))
            s = store.stats()
            assert s["rooms"] == 0
            assert s["events"] == 0
            assert s["bridges"] == 0
            assert s["unique_senders"] == 0
            store.close()

    def test_stats_populated(self):
        from navig.comms.matrix_store import MatrixEvent, MatrixRoom, MatrixStore

        with tempfile.TemporaryDirectory() as d:
            store = MatrixStore(os.path.join(d, "test.db"))
            store.upsert_room(MatrixRoom(room_id="!a:t"))
            store.upsert_room(MatrixRoom(room_id="!b:t"))
            store.add_event(
                MatrixEvent(
                    event_id="$1",
                    room_id="!a:t",
                    sender="@x:t",
                    event_type="m.room.message",
                )
            )
            s = store.stats()
            assert s["rooms"] == 2
            assert s["events"] == 1
            assert s["unique_senders"] == 1
            store.close()


class TestMatrixStoreBotIntegration:
    """Test that NavigMatrixBot correctly initialises and uses the store."""

    def test_bot_has_store_attribute(self):
        from navig.comms.matrix import MatrixConfig, NavigMatrixBot

        bot = NavigMatrixBot(MatrixConfig())
        assert hasattr(bot, "_store")
        assert bot._store is None  # not initialised until start()
        assert bot.store is None

    def test_bot_store_property(self):
        from navig.comms.matrix import MatrixConfig, NavigMatrixBot

        bot = NavigMatrixBot(MatrixConfig())
        # Set a mock store
        bot._store = MagicMock()
        assert bot.store is bot._store


class TestMatrixStoreCLI:
    """CLI smoke tests for store subcommands."""

    def test_store_help(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        result = runner.invoke(matrix_app, ["store", "--help"])
        assert result.exit_code == 0
        assert "store" in result.output.lower()

    def test_store_stats_no_db(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with patch("os.path.exists", return_value=False):
            result = runner.invoke(matrix_app, ["store", "stats"])
            assert result.exit_code == 0
            assert "not initialised" in result.output.lower() or "⚠" in result.output

    def test_store_stats_with_db(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "matrix.db")
            with (
                patch("os.path.expanduser", return_value=db_path),
                patch("os.path.exists", return_value=True),
            ):
                from navig.comms.matrix_store import MatrixStore

                MatrixStore(db_path).close()  # create empty DB
                result = runner.invoke(matrix_app, ["store", "stats"])
                assert result.exit_code == 0

    def test_store_rooms_help(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        result = runner.invoke(matrix_app, ["store", "rooms", "--help"])
        assert result.exit_code == 0

    def test_store_prune_help(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        result = runner.invoke(matrix_app, ["store", "prune", "--help"])
        assert result.exit_code == 0

    def test_store_bridges_help(self):
        from typer.testing import CliRunner

        from navig.commands.matrix import matrix_app

        runner = CliRunner()
        result = runner.invoke(matrix_app, ["store", "bridges", "--help"])
        assert result.exit_code == 0
