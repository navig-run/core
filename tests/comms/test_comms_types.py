"""Tests for navig.comms.types — pure dataclass and enum logic, no I/O."""

from __future__ import annotations

from datetime import datetime

import pytest

from navig.comms.types import (
    DeliveryPriority,
    DeliveryResult,
    FanoutResult,
    NotificationOptions,
    NotificationTarget,
)

# ---------------------------------------------------------------------------
# NotificationTarget
# ---------------------------------------------------------------------------


class TestNotificationTarget:
    def test_telegram_classmethod(self):
        target = NotificationTarget.telegram(12345)
        assert target.telegram_chat_id == 12345
        assert target.matrix_room_id is None
        assert target.user_id is None

    def test_matrix_classmethod(self):
        target = NotificationTarget.matrix("!room:example.org")
        assert target.matrix_room_id == "!room:example.org"
        assert target.telegram_chat_id is None

    def test_auto_classmethod(self):
        target = NotificationTarget.auto("user-abc")
        assert target.user_id == "user-abc"
        assert target.telegram_chat_id is None
        assert target.matrix_room_id is None

    def test_extra_defaults_to_empty_dict(self):
        target = NotificationTarget.telegram(1)
        assert target.extra == {}

    def test_extra_separate_instances(self):
        a = NotificationTarget.telegram(1)
        b = NotificationTarget.telegram(2)
        a.extra["key"] = "val"
        assert "key" not in b.extra

    def test_manual_construction(self):
        target = NotificationTarget(telegram_chat_id=99, user_id="u1")
        assert target.telegram_chat_id == 99
        assert target.user_id == "u1"


# ---------------------------------------------------------------------------
# DeliveryPriority enum
# ---------------------------------------------------------------------------


class TestDeliveryPriority:
    def test_enum_members_exist(self):
        assert DeliveryPriority.LOW.value == "low"
        assert DeliveryPriority.NORMAL.value == "normal"
        assert DeliveryPriority.HIGH.value == "high"
        assert DeliveryPriority.CRITICAL.value == "critical"

    def test_four_members(self):
        assert len(DeliveryPriority) == 4


# ---------------------------------------------------------------------------
# NotificationOptions
# ---------------------------------------------------------------------------


class TestNotificationOptions:
    def test_defaults(self):
        opts = NotificationOptions()
        assert opts.priority == DeliveryPriority.NORMAL
        assert opts.silent is False
        assert opts.ttl_seconds == 0
        assert opts.retry_count == 2
        assert opts.parse_mode == "HTML"
        assert opts.metadata == {}

    def test_custom_values(self):
        opts = NotificationOptions(
            priority=DeliveryPriority.HIGH,
            silent=True,
            ttl_seconds=60,
            retry_count=5,
            parse_mode="MarkdownV2",
        )
        assert opts.priority == DeliveryPriority.HIGH
        assert opts.silent is True
        assert opts.ttl_seconds == 60
        assert opts.retry_count == 5
        assert opts.parse_mode == "MarkdownV2"

    def test_metadata_separate_instances(self):
        a = NotificationOptions()
        b = NotificationOptions()
        a.metadata["x"] = 1
        assert "x" not in b.metadata


# ---------------------------------------------------------------------------
# DeliveryResult
# ---------------------------------------------------------------------------


class TestDeliveryResult:
    def test_success_classmethod(self):
        result = DeliveryResult.success("telegram", "msg-id-123")
        assert result.ok is True
        assert result.channel == "telegram"
        assert result.message_id == "msg-id-123"
        assert result.error is None

    def test_success_without_message_id(self):
        result = DeliveryResult.success("matrix")
        assert result.ok is True
        assert result.message_id is None

    def test_failure_classmethod(self):
        result = DeliveryResult.failure("telegram", "timeout")
        assert result.ok is False
        assert result.channel == "telegram"
        assert result.error == "timeout"
        assert result.message_id is None

    def test_timestamp_is_set(self):
        result = DeliveryResult.success("telegram")
        assert isinstance(result.timestamp, datetime)

    def test_metadata_default_empty(self):
        result = DeliveryResult.success("telegram")
        assert result.metadata == {}

    def test_direct_construction(self):
        result = DeliveryResult(ok=True, channel="matrix")
        assert result.ok is True
        assert result.channel == "matrix"


# ---------------------------------------------------------------------------
# FanoutResult
# ---------------------------------------------------------------------------


class TestFanoutResult:
    def test_all_ok_empty(self):
        fr = FanoutResult()
        # all() on empty iterable is True
        assert fr.all_ok is True

    def test_any_ok_empty(self):
        fr = FanoutResult()
        assert fr.any_ok is False

    def test_all_ok_success(self):
        fr = FanoutResult(
            results=[
                DeliveryResult.success("telegram"),
                DeliveryResult.success("matrix"),
            ]
        )
        assert fr.all_ok is True
        assert fr.any_ok is True

    def test_all_ok_mixed(self):
        fr = FanoutResult(
            results=[
                DeliveryResult.success("telegram"),
                DeliveryResult.failure("matrix", "unreachable"),
            ]
        )
        assert fr.all_ok is False
        assert fr.any_ok is True

    def test_all_ok_all_failed(self):
        fr = FanoutResult(
            results=[
                DeliveryResult.failure("telegram", "err1"),
                DeliveryResult.failure("matrix", "err2"),
            ]
        )
        assert fr.all_ok is False
        assert fr.any_ok is False

    def test_results_default_empty(self):
        fr = FanoutResult()
        assert fr.results == []

    def test_separate_instances(self):
        a = FanoutResult()
        b = FanoutResult()
        a.results.append(DeliveryResult.success("telegram"))
        assert len(b.results) == 0
