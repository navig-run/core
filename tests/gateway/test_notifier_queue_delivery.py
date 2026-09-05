"""The notification queue is a delivery PROMISE — a rejected send must not drop it.

`send()` returns True for a queued HIGH notification meaning "accepted for
delivery", not "sent". The drain then called `_send_notification(n)` and
`self.queue.remove(n)` — throwing away the very bool whose docstring says
"so callers don't report a phantom success". So one Telegram rejection (a 429
when a burst of alerts goes out at once, a network blip) discarded the alert
permanently, leaving a log line as the only trace.

The sibling suite (`test_notifier_send_honesty.py`) pins the PRODUCER: that
`_send_notification` / `_send_batched` report the real result. This one pins the
CONSUMER — that the drain acts on it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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


def _note(title: str = "DNS is down", priority=None):
    from navig.gateway.notifications import Notification, NotificationPriority

    return Notification(
        type="alert",
        title=title,
        message="resolver unreachable",
        priority=priority or NotificationPriority.HIGH,
    )


async def _cancel(task: asyncio.Task | None) -> None:
    """Cancel and reap a task so it cannot outlive the test.

    Bounded on purpose. `_flush_batch_after_delay` deliberately SWALLOWS the
    first cancellation (it flushes what is buffered before giving up), so a bare
    `await task` here blocks on that final send — and if the send is the one
    hanging, the test hangs instead of failing. A hung test reports nothing; a
    failed one reports the bug. `wait_for` re-cancels, which lands inside the
    send and propagates.
    """
    if task is None:
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=2)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


# ── the queue drain ────────────────────────────────────────────────────────────


async def test_rejected_notification_stays_queued():
    """The core of it: a rejected send leaves the notification queued."""
    notifier, ch = _notifier(send_return=None)  # None = rejected, no exception
    n = _note()
    notifier.queue.append(n)

    await notifier._process_queue()

    ch.send_message.assert_awaited_once()  # it DID try
    assert len(notifier.queue) == 1 and notifier.queue[0] is n, (
        "a rejected notification must survive for another attempt, not be dropped"
    )
    assert n.delivery_attempts == 1


async def test_requeued_notification_is_delivered_on_a_later_tick():
    """The retry is the whole point — it must actually go out once the channel
    recovers, which is the common case for a rate-limit rejection."""
    notifier, ch = _notifier(send_return=None)
    notifier.queue.append(_note())

    await notifier._process_queue()  # rejected → requeued
    ch.send_message.return_value = {"message_id": 7}  # channel recovers
    await notifier._process_queue()

    assert ch.send_message.await_count == 2
    assert notifier.queue == [], "a delivered notification must leave the queue"


async def test_undeliverable_notification_is_dropped_loudly(navig_log_capture):
    """Bounded: a notification the channel will never accept must not wedge the
    queue forever — but it leaves with an ERROR, not in silence.

    navig_log_capture (not caplog): navig's loggers set propagate=False.
    """
    notifier, ch = _notifier(send_return=None)
    notifier.queue.append(_note())

    # A fixed bound above any sane attempt budget, so this fails on BEHAVIOUR
    # (only one send was ever attempted) rather than on a missing constant.
    for _ in range(6):
        await notifier._process_queue()

    assert ch.send_message.await_count > 1, "the notification must be retried at all"
    assert ch.send_message.await_count <= 6, "…but not on an unbounded loop"
    assert notifier.queue == [], "the queue must drain even when delivery is hopeless"
    assert any("DROPPED after" in m for m in navig_log_capture), (
        "giving up on a notification is exactly the moment to say so"
    )


async def test_delivered_notification_is_removed():
    notifier, ch = _notifier(send_return={"message_id": 7})
    notifier.queue.append(_note())

    await notifier._process_queue()

    ch.send_message.assert_awaited_once()
    assert notifier.queue == []


async def test_suppressed_notification_is_not_retried():
    """Quiet hours is an intentional skip, not a delivery failure — retrying it
    would resurrect exactly the notifications the user asked not to receive."""
    notifier, ch = _notifier(send_return={"message_id": 7})
    notifier._should_suppress = lambda _n: True
    notifier.queue.append(_note())

    await notifier._process_queue()

    ch.send_message.assert_not_awaited()
    assert notifier.queue == []


async def test_equal_notifications_settle_independently():
    """Two alerts raised in the same tick can compare EQUAL (same type/title/
    message/priority/created_at). `list.remove()` matches on equality, so it
    would drop the wrong row — settling one would silently delete the other and
    leave the failed one queued under a stranger's identity."""
    from navig.gateway.notifications import Notification, NotificationPriority

    notifier, ch = _notifier(send_return=None)
    stamp = datetime(2026, 1, 1, 9, 0, 0)
    kw = dict(
        type="alert",
        title="same",
        message="same",
        priority=NotificationPriority.HIGH,
        created_at=stamp,
    )
    a, b = Notification(**kw), Notification(**kw)
    assert a == b and a is not b, "the premise: equal but distinct"

    notifier.queue.extend([a, b])
    ch.send_message = AsyncMock(side_effect=[None, {"message_id": 1}])  # a fails, b lands

    await notifier._process_queue()

    assert len(notifier.queue) == 1
    assert notifier.queue[0] is a, "the FAILED notification is the one that stays"
    assert (a.delivery_attempts, b.delivery_attempts) == (1, 0)


async def test_low_notification_older_than_a_day_is_flushed():
    """`timedelta.seconds` is the sub-day remainder, not the total. A LOW
    notification sitting for 24h05m reported 300 seconds old, failed the
    `> 1800` age check it had passed a day earlier, and waited for two more to
    arrive before anyone would see it."""
    from navig.gateway.notifications import NotificationPriority

    notifier, ch = _notifier(send_return={"message_id": 7})
    n = _note(priority=NotificationPriority.LOW)
    n.created_at = datetime.now() - timedelta(days=1, minutes=5)
    notifier.queue.append(n)

    await notifier._process_queue()

    ch.send_message.assert_awaited_once()
    assert notifier.queue == []


async def test_send_batched_reports_delivery():
    notifier_ok, _ = _notifier(send_return={"message_id": 7})
    assert await notifier_ok._send_batched([_note()]) is True

    notifier_bad, _ = _notifier(send_return=None)
    assert await notifier_bad._send_batched([_note()]) is False

    # Nothing to lose is not a failure.
    assert await notifier_bad._send_batched([]) is True


# ── the batch buffer (NORMAL/LOW never enter self.queue) ───────────────────────


async def test_rejected_batch_returns_to_the_buffer():
    """NORMAL/LOW live only in `_batch_buffer`; the flush drains and clears it,
    so a rejected flush used to lose the whole batch at once."""
    from navig.gateway.notifications import NotificationPriority

    notifier, _ = _notifier(send_return=None)
    notifier._batch_window_sec = 0
    a = _note("first", NotificationPriority.LOW)
    b = _note("second", NotificationPriority.LOW)
    notifier._batch_buffer.extend([a, b])

    await notifier._flush_batch_after_delay()

    assert [x.title for x in notifier._batch_buffer] == ["first", "second"], (
        "a rejected batch must come back, in arrival order"
    )
    assert (a.delivery_attempts, b.delivery_attempts) == (1, 1)
    await _cancel(notifier._batch_timer)


async def test_requeue_arms_a_timer_even_while_one_is_running():
    """`_requeue_batch` runs INSIDE the flush task, so `self._batch_timer` is
    neither None nor done() — the usual "already armed?" guard would decline to
    schedule and strand the retries until some unrelated notification happened
    to arm a timer."""
    from navig.gateway.notifications import NotificationPriority

    notifier, _ = _notifier(send_return=None)
    notifier._batch_window_sec = 3600
    running = asyncio.create_task(asyncio.sleep(3600))  # stands in for the flush task
    notifier._batch_timer = running

    notifier._requeue_batch([_note("x", NotificationPriority.LOW)])

    assert notifier._batch_timer is not running, "retries must get their own window"
    await _cancel(notifier._batch_timer)
    await _cancel(running)


async def test_cancelled_flush_does_not_arm_a_timer():
    """Shutdown cancels the flush task. It still delivers what is buffered, but
    arming a fresh timer there would leave a task running past stop()."""
    from navig.gateway.notifications import NotificationPriority

    notifier, _ = _notifier(send_return=None)
    n = _note("x", NotificationPriority.LOW)

    notifier._requeue_batch([n], rearm=False)

    assert notifier._batch_buffer[0] is n, "the batch is still kept"
    assert notifier._batch_timer is None, "but nothing is scheduled after shutdown"


async def test_stop_reaps_the_pending_batch_flush():
    """`stop()` cancelled the scheduler task but never the batch timer, so a
    pending 30s flush outlived shutdown and woke up to send on a channel that
    was already tearing down. With delivery retries it could also arm a
    successor — a chain of tasks running past stop()."""
    from navig.gateway.notifications import NotificationPriority

    notifier, ch = _notifier(send_return={"message_id": 7})
    notifier._batch_window_sec = 3600
    notifier._batch_buffer.append(_note("x", NotificationPriority.LOW))
    notifier._batch_timer = asyncio.create_task(notifier._flush_batch_after_delay())
    await asyncio.sleep(0)  # let it reach the sleep
    timer = notifier._batch_timer

    try:
        await asyncio.wait_for(notifier.stop(), timeout=5)
        assert timer.done(), "a pending flush must not outlive stop()"
        assert notifier._batch_timer is None
        ch.send_message.assert_awaited_once()  # the last-gasp flush still delivered
    finally:
        await _cancel(timer)


async def test_stop_is_not_wedged_by_a_hanging_flush():
    """That last-gasp flush is a network call — it gets a grace period, not a
    veto over shutdown."""
    from navig.gateway.notifications import NotificationPriority

    notifier, ch = _notifier(send_return=None)
    notifier._batch_window_sec = 3600
    notifier._SHUTDOWN_DRAIN_SEC = 0.1

    async def _hang(*_a, **_kw):
        await asyncio.sleep(3600)

    ch.send_message = AsyncMock(side_effect=_hang)
    notifier._batch_buffer.append(_note("x", NotificationPriority.LOW))
    notifier._batch_timer = asyncio.create_task(notifier._flush_batch_after_delay())
    await asyncio.sleep(0)
    timer = notifier._batch_timer

    try:
        # wait_for, so a wedged stop() FAILS the test instead of hanging it.
        await asyncio.wait_for(notifier.stop(), timeout=5)
        assert notifier._batch_timer is None
    finally:
        await _cancel(timer)


# ── the other ChannelNotifier: Matrix had the same drop ────────────────────────


def _matrix(send_notice_effect=None):
    from navig.gateway.matrix_notifier import MatrixNotifier

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value="$evt1")
    bot.send_notice = AsyncMock(
        side_effect=send_notice_effect,
        **({} if send_notice_effect else {"return_value": "$evt2"}),
    )
    return MatrixNotifier(bot, "!room:local"), bot


async def test_matrix_rejected_batch_returns_to_the_buffer():
    """`_flush_batch` clears the buffer BEFORE the send, so a raise lost every
    batched notification at once. Batched LOW is exactly the priority whose
    `send()` already returned True ("accepted"), so nothing upstream noticed."""
    from navig.gateway.notifications import NotificationPriority

    notifier, bot = _matrix(send_notice_effect=RuntimeError("matrix is down"))
    a = _note("first", NotificationPriority.LOW)
    b = _note("second", NotificationPriority.LOW)
    assert await notifier.send(a) is True  # accepted for delivery
    assert await notifier.send(b) is True

    assert await notifier._flush_batch() is False
    bot.send_notice.assert_awaited_once()  # it DID try

    assert [x.title for x in notifier._batch_buffer] == ["first", "second"], (
        "a rejected Matrix batch must survive for the next window"
    )
    assert (a.delivery_attempts, b.delivery_attempts) == (1, 1)


async def test_matrix_batch_is_delivered_on_a_later_window():
    from navig.gateway.notifications import NotificationPriority

    notifier, bot = _matrix(send_notice_effect=RuntimeError("matrix is down"))
    await notifier.send(_note("x", NotificationPriority.LOW))

    await notifier._flush_batch()  # rejected → requeued
    bot.send_notice = AsyncMock(return_value="$evt")  # server recovers

    assert await notifier._flush_batch() is True
    assert notifier._batch_buffer == [], "a delivered batch leaves the buffer"


async def test_matrix_undeliverable_batch_is_dropped_loudly(navig_log_capture):
    from navig.gateway.notifications import NotificationPriority

    notifier, _ = _matrix(send_notice_effect=RuntimeError("matrix is down"))
    await notifier.send(_note("x", NotificationPriority.LOW))

    for _ in range(6):
        await notifier._flush_batch()

    assert notifier._batch_buffer == [], "the buffer must not grow forever"
    assert any("DROPPED after" in m for m in navig_log_capture)


async def test_matrix_empty_flush_is_not_a_failure():
    notifier, bot = _matrix()
    assert await notifier._flush_batch() is True
    bot.send_notice.assert_not_awaited()


async def test_both_channels_share_one_attempt_budget():
    """Two independent copies of this constant is how the two drains drifted
    apart — one grew a retry, the other kept dropping."""
    from navig.gateway.matrix_notifier import MatrixNotifier
    from navig.gateway.notifications import ChannelNotifier, TelegramNotifier

    assert (
        TelegramNotifier._MAX_DELIVERY_ATTEMPTS
        is MatrixNotifier._MAX_DELIVERY_ATTEMPTS
        is ChannelNotifier._MAX_DELIVERY_ATTEMPTS
    )


async def test_batch_attempts_reset_after_a_delivered_flush():
    """A notification that failed once and then went out must not carry its
    attempt count into a future batch and get dropped early."""
    from navig.gateway.notifications import NotificationPriority

    notifier, ch = _notifier(send_return=None)
    notifier._batch_window_sec = 0
    a = _note("a", NotificationPriority.LOW)
    b = _note("b", NotificationPriority.LOW)
    notifier._batch_buffer.extend([a, b])

    await notifier._flush_batch_after_delay()  # rejected
    await _cancel(notifier._batch_timer)
    assert a.delivery_attempts == 1

    ch.send_message.return_value = {"message_id": 7}
    await notifier._flush_batch_after_delay()  # delivered

    assert notifier._batch_buffer == []
    assert (a.delivery_attempts, b.delivery_attempts) == (0, 0)
