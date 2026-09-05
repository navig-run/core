"""A reference to the notifier taken at gateway boot must still be the LIVE one.

`NavigGateway._init_comms` runs once, at boot, and stores `channel._notifier` in a
module-level global inside `navig.comms.dispatch`. The health monitor restarts a stale
channel through `_restart_channel`, which restarts it **in place** — `stop()` then
`start()` on the same channel object.

`_start_notifier` used to assign a brand-new `TelegramNotifier` on every start, so one
restart left that global pointing at a notifier whose `stop()` had already cancelled its
scheduler loop. The damage is priority-shaped:

* CRITICAL — sent inline by `send()`. Still delivered.
* NORMAL / LOW — buffered, and their flush timer is created per batch. Still delivered.
* **HIGH — appended to `self.queue`, which ONLY `_scheduler_loop` drains.** Queued
  forever, while `send()` returns True meaning "accepted for delivery".

So the failure is invisible from the caller and selective enough to look like anything
but a restart bug.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def quiet_scheduler(monkeypatch: pytest.MonkeyPatch):
    """Replace the scheduler loop with an inert sleep.

    The real loop reads config and stores on its first tick; this test is about object
    identity and task liveness, so keep it deterministic and side-effect free.
    """
    from navig.gateway.notifications import TelegramNotifier

    async def _idle(self):
        await asyncio.sleep(3600)

    monkeypatch.setattr(TelegramNotifier, "_scheduler_loop", _idle, raising=True)


def _channel():
    from navig.gateway.channels.telegram import TelegramChannel

    return TelegramChannel(bot_token="123:TEST", allowed_users=[4242])


async def _shutdown(notifier):
    if notifier is not None:
        await notifier.stop()


async def test_boot_reference_still_points_at_the_live_notifier(quiet_scheduler):
    ch = _channel()
    await ch._start_notifier()
    captured = ch._notifier  # what _init_comms hands to comms.dispatch, once
    assert captured is not None

    # What _restart_channel does: stop() (which stops the notifier), then start().
    await captured.stop()
    await ch._start_notifier()

    try:
        assert ch._notifier is captured, (
            "the channel swapped in a new notifier — comms.dispatch is left holding a "
            "stopped one, and every HIGH notification queues forever"
        )
        assert captured._running is True
        assert captured._scheduler_task is not None
        assert not captured._scheduler_task.done(), (
            "the captured notifier must have a LIVE scheduler loop; only that drains "
            "the HIGH queue"
        )
    finally:
        await _shutdown(ch._notifier)


async def test_queued_high_priority_work_survives_the_restart(quiet_scheduler):
    """Reusing the object also carries the queue across the blip, instead of
    stranding it on a discarded notifier."""
    from navig.gateway.notifications import Notification, NotificationPriority

    ch = _channel()
    await ch._start_notifier()
    notifier = ch._notifier

    await notifier.send(
        Notification(
            type="alert",
            title="host down",
            message="web-1 unreachable",
            priority=NotificationPriority.HIGH,
        )
    )
    assert len(notifier.queue) == 1

    await notifier.stop()
    await ch._start_notifier()

    try:
        assert ch._notifier is notifier
        assert len(ch._notifier.queue) == 1, "the queued alert must survive the restart"
        assert ch._notifier.queue[0].title == "host down"
    finally:
        await _shutdown(ch._notifier)


async def test_a_changed_default_chat_still_gets_a_new_notifier(quiet_scheduler):
    """Reuse is keyed on the destination. A different `allowed_users` is a different
    chat, not the same notifier restarting."""
    ch = _channel()
    await ch._start_notifier()
    first = ch._notifier

    await first.stop()
    ch.allowed_users = {99}
    await ch._start_notifier()

    try:
        assert ch._notifier is not first
        assert ch._notifier.chat_id == 99
    finally:
        await _shutdown(ch._notifier)
        await _shutdown(first)


async def test_start_does_not_orphan_a_running_scheduler(quiet_scheduler):
    """`start()` is re-entrant now that the restart path reuses the object. Without a
    guard the second call replaces `_scheduler_task` and leaks the first: two loops
    draining one queue, and `stop()` can only reap the newer."""
    ch = _channel()
    await ch._start_notifier()
    notifier = ch._notifier
    first_task = notifier._scheduler_task

    await notifier.start()  # re-entry without an intervening stop()

    try:
        assert notifier._scheduler_task is first_task, "the running loop was replaced"
        assert not first_task.done()
    finally:
        await _shutdown(notifier)

    assert first_task.done(), "stop() must reap the loop it started"
