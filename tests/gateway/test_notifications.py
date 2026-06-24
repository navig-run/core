"""
Tests for navig.gateway.notifications — NotificationPriority, Notification dataclass.
"""

from __future__ import annotations

import pytest

from navig.gateway.notifications import Notification, NotificationPriority


# ─── NotificationPriority ─────────────────────────────────────────────────────


def test_notification_priority_values():
    assert NotificationPriority.LOW.value == 1
    assert NotificationPriority.NORMAL.value == 2
    assert NotificationPriority.HIGH.value == 3
    assert NotificationPriority.CRITICAL.value == 4


def test_notification_priority_ordering():
    assert NotificationPriority.LOW.value < NotificationPriority.NORMAL.value
    assert NotificationPriority.NORMAL.value < NotificationPriority.HIGH.value
    assert NotificationPriority.HIGH.value < NotificationPriority.CRITICAL.value


# ─── Notification defaults ────────────────────────────────────────────────────


def test_notification_defaults():
    n = Notification(type="alert", title="Test", message="Something happened")
    assert n.priority == NotificationPriority.NORMAL
    assert n.metadata == {}
    assert n.keyboard is None
    assert n.raw_message is False


def test_notification_raw_message_default():
    n = Notification(type="heartbeat", title="OK", message="All good")
    assert n.raw_message is False


# ─── Notification.to_telegram_message ────────────────────────────────────────


def test_to_telegram_message_alert():
    n = Notification(type="alert", title="Disk Warning", message="Disk at 90%")
    msg = n.to_telegram_message()
    assert "Disk Warning" in msg
    assert "Disk at 90%" in msg
    assert "🚨" in msg
    assert "<b>" in msg


def test_to_telegram_message_briefing():
    n = Notification(type="briefing", title="Daily Summary", message="Everything fine")
    msg = n.to_telegram_message()
    assert "📊" in msg
    assert "Daily Summary" in msg


def test_to_telegram_message_reminder():
    n = Notification(type="reminder", title="Deploy at 5pm", message="Deploy the app!")
    msg = n.to_telegram_message()
    assert "⏰" in msg


def test_to_telegram_message_heartbeat():
    n = Notification(type="heartbeat", title="Status", message="All systems OK")
    msg = n.to_telegram_message()
    assert "💓" in msg


def test_to_telegram_message_routine():
    n = Notification(type="routine", title="Morning", message="Good morning!")
    msg = n.to_telegram_message()
    assert "☀️" in msg


def test_to_telegram_message_unknown_type():
    n = Notification(type="custom", title="Custom", message="Custom message")
    msg = n.to_telegram_message()
    assert "Custom" in msg
    assert "📢" in msg  # default emoji


def test_to_telegram_message_critical_prefix():
    n = Notification(
        type="alert",
        title="CRITICAL",
        message="Server down",
        priority=NotificationPriority.CRITICAL,
    )
    msg = n.to_telegram_message()
    assert "🔴" in msg


def test_to_telegram_message_high_prefix():
    n = Notification(
        type="alert",
        title="HIGH",
        message="Warning",
        priority=NotificationPriority.HIGH,
    )
    msg = n.to_telegram_message()
    assert "🟡" in msg


def test_to_telegram_message_normal_no_prefix():
    n = Notification(type="alert", title="Normal", message="Info", priority=NotificationPriority.NORMAL)
    msg = n.to_telegram_message()
    assert "🔴" not in msg
    assert "🟡" not in msg


def test_to_telegram_message_raw_mode():
    raw = "<b>Custom formatted</b> message with no wrapper."
    n = Notification(type="alert", title="Ignored", message=raw, raw_message=True)
    msg = n.to_telegram_message()
    assert msg == raw
    # Title not injected
    assert "Ignored" not in msg


def test_to_telegram_message_contains_bold_title():
    n = Notification(type="alert", title="My Title", message="Body text")
    msg = n.to_telegram_message()
    assert "<b>My Title</b>" in msg


def test_to_telegram_message_title_and_message_present():
    n = Notification(type="briefing", title="Header", message="Details here")
    msg = n.to_telegram_message()
    assert "Header" in msg
    assert "Details here" in msg


# ─── Notification with metadata ───────────────────────────────────────────────


def test_notification_metadata_stored():
    n = Notification(
        type="alert",
        title="T",
        message="M",
        metadata={"host": "prod-01", "severity": "high"},
    )
    assert n.metadata["host"] == "prod-01"
    assert n.metadata["severity"] == "high"


def test_notification_keyboard_stored():
    keyboard = [[{"text": "Confirm", "callback_data": "confirm"}]]
    n = Notification(type="alert", title="T", message="M", keyboard=keyboard)
    assert n.keyboard == keyboard
