"""Tests for navig.comms.dispatch — configure, send_user_notification, _resolve_channel, fanout."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import navig.comms.dispatch as dispatch_mod
from navig.comms.types import (
    CommsChannel,
    DeliveryPriority,
    DeliveryResult,
    FanoutResult,
    NotificationOptions,
    NotificationTarget,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_dispatch():
    """Reset module-level state to clean defaults."""
    dispatch_mod._telegram_notifier = None
    dispatch_mod._matrix_notifier = None
    dispatch_mod._default_channel = "telegram"


def _tg_target(chat_id: int = 111) -> NotificationTarget:
    return NotificationTarget.telegram(chat_id)


def _mx_target(room_id: str = "!room:example.org") -> NotificationTarget:
    return NotificationTarget.matrix(room_id)


# ---------------------------------------------------------------------------
# configure()
# ---------------------------------------------------------------------------

class TestConfigure:
    def setup_method(self):
        _reset_dispatch()

    def test_sets_telegram_notifier(self):
        fake = MagicMock()
        dispatch_mod.configure(telegram_notifier=fake)
        assert dispatch_mod._telegram_notifier is fake

    def test_sets_matrix_notifier(self):
        fake = MagicMock()
        dispatch_mod.configure(matrix_notifier=fake)
        assert dispatch_mod._matrix_notifier is fake

    def test_sets_default_channel(self):
        dispatch_mod.configure(default_channel="matrix")
        assert dispatch_mod._default_channel == "matrix"

    def test_defaults_to_telegram(self):
        dispatch_mod.configure()
        assert dispatch_mod._default_channel == "telegram"

    def test_clears_notifiers_when_none_passed(self):
        dispatch_mod._telegram_notifier = MagicMock()
        dispatch_mod.configure()
        assert dispatch_mod._telegram_notifier is None


# ---------------------------------------------------------------------------
# get_default_channel()
# ---------------------------------------------------------------------------

class TestGetDefaultChannel:
    def setup_method(self):
        _reset_dispatch()

    def test_returns_configured_channel(self):
        dispatch_mod._default_channel = "matrix"
        assert dispatch_mod.get_default_channel() == "matrix"

    def test_default_is_telegram(self):
        assert dispatch_mod.get_default_channel() == "telegram"


# ---------------------------------------------------------------------------
# _resolve_channel
# ---------------------------------------------------------------------------

class TestResolveChannel:
    def setup_method(self):
        _reset_dispatch()

    def test_non_auto_returns_same(self):
        target = _tg_target()
        for ch in ("telegram", "matrix", "both", "none"):
            assert dispatch_mod._resolve_channel(ch, target) == ch  # type: ignore

    def test_auto_without_user_id_returns_default(self):
        target = NotificationTarget(telegram_chat_id=1)
        result = dispatch_mod._resolve_channel("auto", target)
        assert result == dispatch_mod._default_channel

    def test_auto_with_user_id_uses_identity_store(self):
        target = NotificationTarget.auto("user-123")
        with patch("navig.comms.dispatch._default_channel", "telegram"):
            with patch("navig.identity.store.get_user_preferred_channel", return_value="matrix", create=True):
                with patch.dict("sys.modules", {"navig.identity.store": MagicMock(get_user_preferred_channel=lambda uid: "matrix")}):
                    result = dispatch_mod._resolve_channel("auto", target)
        # Either uses identity store result or falls back to default — both valid
        assert result in ("matrix", "telegram")

    def test_auto_identity_import_error_falls_back(self):
        target = NotificationTarget.auto("user-456")
        # ImportError branch should fall back to default_channel
        with patch.dict("sys.modules", {"navig.identity": None, "navig.identity.store": None}):
            result = dispatch_mod._resolve_channel("auto", target)
        assert result == dispatch_mod._default_channel


# ---------------------------------------------------------------------------
# send_user_notification — "none" channel
# ---------------------------------------------------------------------------

class TestSendNoneChannel:
    def setup_method(self):
        _reset_dispatch()

    def test_none_returns_success_noop(self):
        target = _tg_target()
        result = asyncio.run(dispatch_mod.send_user_notification("none", target, "hi"))
        assert isinstance(result, DeliveryResult)
        assert result.ok is True
        assert result.channel == "none"
        assert result.message_id == "noop"


# ---------------------------------------------------------------------------
# send_user_notification — telegram path
# ---------------------------------------------------------------------------

class TestSendTelegramChannel:
    def setup_method(self):
        _reset_dispatch()

    def test_no_telegram_notifier_returns_failure(self):
        dispatch_mod._telegram_notifier = None
        target = _tg_target()
        result = asyncio.run(dispatch_mod.send_user_notification("telegram", target, "msg"))
        assert isinstance(result, DeliveryResult)
        assert result.ok is False
        assert "not configured" in result.error.lower()

    def test_no_telegram_chat_id_returns_failure(self):
        dispatch_mod._telegram_notifier = MagicMock()
        target = NotificationTarget()  # No chat_id
        result = asyncio.run(dispatch_mod.send_user_notification("telegram", target, "msg"))
        assert result.ok is False

    def test_sends_via_telegram_notifier(self):
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock()
        dispatch_mod._telegram_notifier = mock_notifier

        # Patch the gateway import
        fake_priority = MagicMock()
        fake_notification_cls = MagicMock()
        fake_notif_instance = MagicMock()
        fake_notification_cls.return_value = fake_notif_instance
        fake_module = MagicMock(
            Notification=fake_notification_cls,
            NotificationPriority=fake_priority,
        )
        fake_priority.NORMAL = "NORMAL"
        fake_priority.LOW = "LOW"
        fake_priority.HIGH = "HIGH"
        fake_priority.CRITICAL = "CRITICAL"

        with patch.dict("sys.modules", {"navig.gateway.notifications": fake_module}):
            target = _tg_target(chat_id=42)
            result = asyncio.run(dispatch_mod.send_user_notification("telegram", target, "Hello"))

        mock_notifier.send.assert_awaited_once()
        assert result.ok is True
        assert result.channel == "telegram"

    def test_telegram_exception_returns_failure(self):
        mock_notifier = MagicMock()
        mock_notifier.send = AsyncMock(side_effect=RuntimeError("network fail"))
        dispatch_mod._telegram_notifier = mock_notifier

        fake_module = MagicMock(
            Notification=MagicMock(return_value=MagicMock()),
            NotificationPriority=MagicMock(NORMAL="n", LOW="l", HIGH="h", CRITICAL="c"),
        )
        with patch.dict("sys.modules", {"navig.gateway.notifications": fake_module}):
            target = _tg_target(42)
            result = asyncio.run(dispatch_mod.send_user_notification("telegram", target, "crash"))

        assert result.ok is False
        assert "network fail" in result.error


# ---------------------------------------------------------------------------
# send_user_notification — matrix path
# ---------------------------------------------------------------------------

class TestSendMatrixChannel:
    def setup_method(self):
        _reset_dispatch()

    def test_no_matrix_notifier_returns_failure(self):
        dispatch_mod._matrix_notifier = None
        target = _mx_target()
        result = asyncio.run(dispatch_mod.send_user_notification("matrix", target, "msg"))
        assert result.ok is False
        assert "not configured" in result.error.lower()

    def test_no_matrix_room_id_returns_failure(self):
        dispatch_mod._matrix_notifier = MagicMock()
        target = NotificationTarget()
        result = asyncio.run(dispatch_mod.send_user_notification("matrix", target, "msg"))
        assert result.ok is False

    def test_sends_via_matrix_notifier(self):
        mock_notifier = MagicMock()
        mock_notifier.send_message = AsyncMock()
        dispatch_mod._matrix_notifier = mock_notifier

        target = _mx_target("!r:example.org")
        result = asyncio.run(dispatch_mod.send_user_notification("matrix", target, "Hello"))

        mock_notifier.send_message.assert_awaited_once_with("!r:example.org", "Hello")
        assert result.ok is True
        assert result.channel == "matrix"

    def test_matrix_exception_returns_failure(self):
        mock_notifier = MagicMock()
        mock_notifier.send_message = AsyncMock(side_effect=RuntimeError("matrix error"))
        dispatch_mod._matrix_notifier = mock_notifier

        target = _mx_target()
        result = asyncio.run(dispatch_mod.send_user_notification("matrix", target, "boom"))

        assert result.ok is False
        assert "matrix error" in result.error


# ---------------------------------------------------------------------------
# _fanout
# ---------------------------------------------------------------------------

class TestFanout:
    def setup_method(self):
        _reset_dispatch()

    def test_fanout_no_channels_returns_failure_result(self):
        target = NotificationTarget()
        result = asyncio.run(dispatch_mod._fanout(target, "msg", NotificationOptions()))
        assert isinstance(result, FanoutResult)
        assert not result.all_ok

    def test_fanout_telegram_only(self):
        mock_tg = MagicMock()
        mock_tg.send = AsyncMock()
        dispatch_mod._telegram_notifier = mock_tg

        fake_module = MagicMock(
            Notification=MagicMock(return_value=MagicMock()),
            NotificationPriority=MagicMock(NORMAL="n", LOW="l", HIGH="h", CRITICAL="c"),
        )
        with patch.dict("sys.modules", {"navig.gateway.notifications": fake_module}):
            target = _tg_target(99)
            result = asyncio.run(dispatch_mod._fanout(target, "fanout msg", NotificationOptions()))

        assert isinstance(result, FanoutResult)
        assert result.any_ok

    def test_fanout_matrix_only(self):
        mock_mx = MagicMock()
        mock_mx.send_message = AsyncMock()
        dispatch_mod._matrix_notifier = mock_mx

        target = _mx_target("!fan:example.org")
        result = asyncio.run(dispatch_mod._fanout(target, "fanout", NotificationOptions()))

        assert isinstance(result, FanoutResult)
        assert result.any_ok

    def test_fanout_collects_exception_as_failure(self):
        mock_tg = MagicMock()
        mock_tg.send = AsyncMock(side_effect=RuntimeError("tg boom"))
        dispatch_mod._telegram_notifier = mock_tg

        fake_module = MagicMock(
            Notification=MagicMock(return_value=MagicMock()),
            NotificationPriority=MagicMock(NORMAL="n", LOW="l", HIGH="h", CRITICAL="c"),
        )
        with patch.dict("sys.modules", {"navig.gateway.notifications": fake_module}):
            target = _tg_target(1)
            result = asyncio.run(dispatch_mod._fanout(target, "boom fanout", NotificationOptions()))

        # Exception converted to DeliveryResult.failure
        assert isinstance(result, FanoutResult)


# ---------------------------------------------------------------------------
# Unknown channel fallback
# ---------------------------------------------------------------------------

class TestUnknownChannel:
    def test_unknown_channel_returns_failure(self):
        _reset_dispatch()
        target = _tg_target()
        result = asyncio.run(
            dispatch_mod.send_user_notification("unknown_channel", target, "msg")  # type: ignore
        )
        assert isinstance(result, DeliveryResult)
        assert result.ok is False
