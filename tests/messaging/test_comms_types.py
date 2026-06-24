"""Tests for navig.comms.types — NotificationTarget, DeliveryPriority, DeliveryResult, FanoutResult."""
from __future__ import annotations

import pytest

from navig.comms.types import (
    DeliveryPriority,
    DeliveryResult,
    FanoutResult,
    NotificationOptions,
    NotificationTarget,
)


class TestNotificationTarget:
    def test_telegram_factory(self) -> None:
        t = NotificationTarget.telegram(12345)
        assert t.telegram_chat_id == 12345

    def test_matrix_factory(self) -> None:
        t = NotificationTarget.matrix("!room:server.org")
        assert t.matrix_room_id == "!room:server.org"

    def test_auto_factory(self) -> None:
        t = NotificationTarget.auto("user1")
        assert t.user_id == "user1"

    def test_telegram_has_no_matrix(self) -> None:
        t = NotificationTarget.telegram(99)
        assert not t.matrix_room_id

    def test_matrix_has_no_telegram(self) -> None:
        t = NotificationTarget.matrix("!r:s")
        assert not t.telegram_chat_id


class TestDeliveryPriority:
    def test_critical_value(self) -> None:
        assert DeliveryPriority.CRITICAL.value == "critical"

    def test_high_value(self) -> None:
        assert DeliveryPriority.HIGH.value == "high"

    def test_normal_value(self) -> None:
        assert DeliveryPriority.NORMAL.value == "normal"

    def test_low_value(self) -> None:
        assert DeliveryPriority.LOW.value == "low"

    def test_all_members(self) -> None:
        names = {p.name for p in DeliveryPriority}
        assert {"LOW", "NORMAL", "HIGH", "CRITICAL"}.issubset(names)


class TestDeliveryResult:
    def test_success_factory_ok(self) -> None:
        r = DeliveryResult.success("telegram", "msg123")
        assert r.ok is True

    def test_success_factory_channel(self) -> None:
        r = DeliveryResult.success("telegram", "msg123")
        assert r.channel == "telegram"

    def test_success_factory_message_id(self) -> None:
        r = DeliveryResult.success("telegram", "msg123")
        assert r.message_id == "msg123"

    def test_failure_factory_not_ok(self) -> None:
        r = DeliveryResult.failure("matrix", "timeout")
        assert r.ok is False

    def test_failure_factory_error(self) -> None:
        r = DeliveryResult.failure("matrix", "timeout")
        assert r.error == "timeout"

    def test_failure_factory_channel(self) -> None:
        r = DeliveryResult.failure("matrix", "error")
        assert r.channel == "matrix"

    def test_success_has_no_error(self) -> None:
        r = DeliveryResult.success("ch", "mid")
        assert not r.error


class TestFanoutResult:
    def test_all_ok_empty(self) -> None:
        fr = FanoutResult()
        assert fr.all_ok is True

    def test_all_ok_true(self) -> None:
        fr = FanoutResult(results=[
            DeliveryResult.success("telegram", "a"),
            DeliveryResult.success("matrix", "b"),
        ])
        assert fr.all_ok is True

    def test_all_ok_false_when_any_fail(self) -> None:
        fr = FanoutResult(results=[
            DeliveryResult.success("telegram", "a"),
            DeliveryResult.failure("matrix", "err"),
        ])
        assert fr.all_ok is False

    def test_any_ok_empty(self) -> None:
        fr = FanoutResult()
        assert fr.any_ok is False

    def test_any_ok_true_with_one_success(self) -> None:
        fr = FanoutResult(results=[
            DeliveryResult.failure("telegram", "err"),
            DeliveryResult.success("matrix", "mid"),
        ])
        assert fr.any_ok is True

    def test_any_ok_false_all_failed(self) -> None:
        fr = FanoutResult(results=[
            DeliveryResult.failure("telegram", "e1"),
            DeliveryResult.failure("matrix", "e2"),
        ])
        assert fr.any_ok is False

    def test_results_accessible(self) -> None:
        r = DeliveryResult.success("telegram", "id1")
        fr = FanoutResult(results=[r])
        assert fr.results[0] is r


class TestNotificationOptions:
    def test_defaults(self) -> None:
        opts = NotificationOptions()
        assert opts.priority in (None, DeliveryPriority.NORMAL)

    def test_critical_priority(self) -> None:
        opts = NotificationOptions(priority=DeliveryPriority.CRITICAL)
        assert opts.priority == DeliveryPriority.CRITICAL

    def test_silent_flag(self) -> None:
        opts = NotificationOptions(silent=True)
        assert opts.silent is True

    def test_retry_count(self) -> None:
        opts = NotificationOptions(retry_count=3)
        assert opts.retry_count == 3
