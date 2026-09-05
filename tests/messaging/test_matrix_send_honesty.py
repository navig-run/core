"""The Matrix transport reports failure by RETURNING None, never by raising.

``NavigMatrixBot.send_message`` / ``send_notice`` are documented "Returns event_id or
None" and produce None for every failure mode there is:

* ``self._client`` is not initialised — the bot never connected;
* the server answered with something other than a ``RoomSendResponse``;
* an exception, which the bot catches ITSELF (``except Exception: logger.exception(...);
  return None``).

That last one is what made the class invisible. Every daemon-side consumer wrapped the
call in `try/except` and returned success on the non-exception path — so the `except`
arm was unreachable for a failed send, and the success path covered it. A Matrix bot
that never connected reported every notification as delivered.

`navig matrix send` (the CLI) has always done it correctly — `result = await
bot.send_message(...)` then `if result:` — which is what shows the contract was known,
just not honoured off the CLI path.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


# ── comms.dispatch ─────────────────────────────────────────────────────────────


def _dispatch_with(send_return):
    import navig.comms.dispatch as dispatch_mod

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=send_return)
    dispatch_mod._matrix_notifier = bot
    return dispatch_mod, bot


async def test_dispatch_reports_failure_when_the_bot_returns_no_event_id(monkeypatch):
    from navig.comms.types import NotificationTarget

    dispatch_mod, bot = _dispatch_with(None)
    monkeypatch.setattr(dispatch_mod, "_matrix_notifier", bot)

    result = await dispatch_mod.send_user_notification(
        "matrix", NotificationTarget.matrix("!room:example.org"), "disk at 98%"
    )

    bot.send_message.assert_awaited_once()  # it DID try
    assert result.ok is False, (
        "a bot that returned no event id did not deliver anything"
    )


async def test_dispatch_reports_success_and_keeps_the_event_id(monkeypatch):
    from navig.comms.types import NotificationTarget

    dispatch_mod, bot = _dispatch_with("$evt42")
    monkeypatch.setattr(dispatch_mod, "_matrix_notifier", bot)

    result = await dispatch_mod.send_user_notification(
        "matrix", NotificationTarget.matrix("!room:example.org"), "ok"
    )

    assert result.ok is True
    assert result.message_id == "$evt42", "the event id is the only delivery evidence"


# ── MatrixNotifier ─────────────────────────────────────────────────────────────


def _notifier(send_return):
    from navig.gateway.matrix_notifier import MatrixNotifier

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=send_return)
    bot.send_notice = AsyncMock(return_value=send_return)
    return MatrixNotifier(bot, "!room:local"), bot


def _note(priority=None):
    from navig.gateway.notifications import Notification, NotificationPriority

    return Notification(
        type="alert",
        title="DNS is down",
        message="resolver unreachable",
        priority=priority or NotificationPriority.HIGH,
    )


@pytest.mark.parametrize("critical", [True, False])
async def test_send_now_is_false_when_no_event_id_comes_back(critical: bool):
    """CRITICAL goes through send_message, everything else through send_notice —
    both signal failure the same way."""
    from navig.gateway.notifications import NotificationPriority

    notifier, _bot = _notifier(None)
    prio = NotificationPriority.CRITICAL if critical else NotificationPriority.HIGH

    assert await notifier._send_now(_note(prio), "!room:local") is False


async def test_send_now_is_true_on_a_real_event_id():
    notifier, _bot = _notifier("$evt1")
    assert await notifier._send_now(_note(), "!room:local") is True


async def test_flush_batch_requeues_when_no_event_id_comes_back():
    """The retry machinery existed but was unreachable: it hung off the `except`
    arm, and this transport does not raise."""
    from navig.gateway.notifications import NotificationPriority

    notifier, bot = _notifier(None)
    a = _note(NotificationPriority.LOW)
    assert await notifier.send(a) is True  # accepted for delivery (batched)

    assert await notifier._flush_batch() is False
    bot.send_notice.assert_awaited_once()  # it DID try
    assert notifier._batch_buffer == [a], "a rejected batch must come back"


async def test_flush_batch_clears_on_a_real_event_id():
    from navig.gateway.notifications import NotificationPriority

    notifier, _bot = _notifier("$evt1")
    await notifier.send(_note(NotificationPriority.LOW))

    assert await notifier._flush_batch() is True
    assert notifier._batch_buffer == []


# ── MatrixHitLChannel ──────────────────────────────────────────────────────────


def _hitl(send_return):
    from navig.integrations.comms_router import MatrixHitLChannel

    bot = MagicMock()
    bot.is_running = True
    bot.send_message = AsyncMock(return_value=send_return)
    bot.on_message = MagicMock()
    return MatrixHitLChannel(lambda: bot, "!room:local"), bot


async def test_notify_counts_a_no_event_id_send_as_a_failure():
    """`is_running` proves the bot started, not that the message landed — so the
    health counter was fed a success for every undelivered notification."""
    ch, _bot = _hitl(None)

    assert await ch.notify("backup finished") is False
    assert ch._consecutive_failures == 1


async def test_notify_records_success_on_a_real_event_id():
    ch, _bot = _hitl("$evt1")

    assert await ch.notify("backup finished") is True
    assert ch._consecutive_failures == 0


async def test_ask_does_not_wait_for_a_reply_it_never_asked_for():
    """Nobody was asked, so nobody will answer. Waiting the full timeout made an
    UNDELIVERED question look like an ignored one, and held the router up before it
    tried the next channel."""
    ch, _bot = _hitl(None)

    started = time.monotonic()
    reply = await asyncio.wait_for(ch.ask("Proceed with the deploy?", timeout=30), timeout=5)
    elapsed = time.monotonic() - started

    assert reply == ""
    assert elapsed < 2, f"returned only after waiting ({elapsed:.1f}s)"
    assert ch._consecutive_failures == 1, "an undelivered question is a channel failure"


# ── the contract itself ────────────────────────────────────────────────────────


async def test_the_bot_returns_none_rather_than_raising_when_unconnected():
    """The premise of everything above: no client means None, not an exception —
    which is why every `try/except` consumer reported success."""
    from navig.comms.matrix import NavigMatrixBot

    bot = NavigMatrixBot.__new__(NavigMatrixBot)  # no connect, no config needed
    bot._client = None

    assert await bot.send_message("!room:local", "hi") is None
    assert await bot.send_notice("!room:local", "hi") is None
