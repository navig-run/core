"""A rejected Telegram send must be SURFACED, not silently treated as delivered.

`TelegramChannel.send_message` returns `None` on a rejected send WITHOUT raising
(rate-limit exhausted, API error, timeout). `TelegramNotifier._send_notification`
/ `_send_batched` used to discard that return, so a proactive alert that Telegram
dropped looked delivered — the "healed at 3am, told nobody" trap. They now log a
WARNING on the falsy return (the reminder poller already checks `if sent:`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


def _notifier(send_return):
    from navig.gateway.notifications import TelegramNotifier

    ch = MagicMock()
    ch.send_message = AsyncMock(return_value=send_return)
    notifier = TelegramNotifier(ch, chat_id=123)
    notifier._should_suppress = lambda _n: False  # don't gate on quiet hours/DND
    return notifier, ch


def _note():
    from navig.gateway.notifications import Notification, NotificationPriority

    return Notification(
        type="alert",
        title="DNS is down",
        message="resolver unreachable",
        priority=NotificationPriority.HIGH,
    )


async def test_send_notification_warns_when_telegram_rejects(navig_log_capture):
    # navig_log_capture (not caplog): navig's loggers set propagate=False, so pytest's caplog
    # never sees these messages — see the fixture docstring in tests/conftest.py.
    notifier, ch = _notifier(send_return=None)  # None = rejected send, no exception
    await notifier._send_notification(_note())

    ch.send_message.assert_awaited_once()  # it DID attempt the send
    assert any("NOT delivered" in m for m in navig_log_capture), (
        "a rejected send must be surfaced, not silently treated as success"
    )


async def test_send_notification_quiet_on_success(navig_log_capture):
    notifier, ch = _notifier(send_return={"message_id": 7})  # delivered
    await notifier._send_notification(_note())

    ch.send_message.assert_awaited_once()
    assert not any("NOT delivered" in m for m in navig_log_capture), (
        "a successful send must not warn"
    )


async def test_send_batched_warns_when_telegram_rejects(navig_log_capture):
    notifier, ch = _notifier(send_return=None)
    await notifier._send_batched([_note(), _note()])

    ch.send_message.assert_awaited_once()
    assert any("NOT delivered" in m for m in navig_log_capture)


async def test_send_notification_returns_delivery_result():
    """_send_notification returns the real result so callers up the chain
    (send / send_alert / the notify router) can report it instead of a phantom
    success. Suppressed (quiet hours) is True — intentional, not a failure."""
    notifier_reject, _ = _notifier(send_return=None)
    assert await notifier_reject._send_notification(_note()) is False

    notifier_ok, _ = _notifier(send_return={"message_id": 7})
    assert await notifier_ok._send_notification(_note()) is True

    notifier_suppressed, ch = _notifier(send_return={"message_id": 7})
    notifier_suppressed._should_suppress = lambda _n: True
    assert await notifier_suppressed._send_notification(_note()) is True
    ch.send_message.assert_not_awaited()


async def test_send_alert_critical_propagates_rejection():
    """A CRITICAL alert is sent immediately, so send_alert must return the REAL
    delivery result — this is what stops the notify router reporting a phantom
    'sent' for a must-deliver alert Telegram rejected."""
    from navig.gateway.notifications import NotificationPriority

    notifier_reject, _ = _notifier(send_return=None)
    assert (
        await notifier_reject.send_alert("DNS down", "resolver unreachable", NotificationPriority.CRITICAL)
        is False
    )

    notifier_ok, _ = _notifier(send_return={"message_id": 7})
    assert await notifier_ok.send_alert("DNS ok", "", NotificationPriority.CRITICAL) is True


async def test_send_alert_high_is_queued_and_reports_accepted():
    """HIGH is delivered asynchronously (queued for the scheduler tick), so
    send_alert returns True ('accepted for delivery') and does not send inline."""
    from navig.gateway.notifications import NotificationPriority

    notifier, ch = _notifier(send_return=None)  # would reject IF sent inline
    ok = await notifier.send_alert("later", "", NotificationPriority.HIGH)
    assert ok is True
    ch.send_message.assert_not_awaited()  # queued, not sent inline
    assert len(notifier.queue) == 1
