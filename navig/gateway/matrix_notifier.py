"""
MatrixNotifier — ChannelNotifier implementation for Matrix.

Bridges the NAVIG notification pipeline to Matrix rooms.
Supports batching, quiet hours, scheduled briefings, and priority routing.
"""

from __future__ import annotations

import asyncio
import logging

from navig.gateway.notifications import (
    ChannelNotifier,
    Notification,
    NotificationPriority,
)

logger = logging.getLogger(__name__)


def _format_for_matrix(notification: Notification) -> str:
    """Format a Notification for Matrix (plain text with unicode icons)."""
    emoji_map = {
        "alert": "\u26a0\ufe0f",  # ⚠️
        "briefing": "\U0001f4ca",  # 📊
        "routine": "\u2600\ufe0f",  # ☀️
        "heartbeat": "\U0001f493",  # 💓
        "reminder": "\u23f0",  # ⏰
    }
    priority_prefix = {
        NotificationPriority.CRITICAL: "\U0001f534 ",  # 🔴
        NotificationPriority.HIGH: "\U0001f7e1 ",  # 🟡
        NotificationPriority.NORMAL: "",
        NotificationPriority.LOW: "",
    }
    emoji = emoji_map.get(notification.type, "\U0001f4e2")  # 📢
    prefix = priority_prefix.get(notification.priority, "")
    return f"{prefix}{emoji} **{notification.title}**\n\n{notification.message}"


class MatrixNotifier(ChannelNotifier):
    """
    Send NAVIG notifications through Matrix.

    Parameters
    ----------
    bot : NavigMatrixBot
        An already-initialised (or about-to-be-started) bot instance.
    room_id : str
        Default room where notifications are sent.
    priority_room_id : str | None
        Optional dedicated room for HIGH / CRITICAL alerts.
    batch_window_sec : int
        How many seconds to batch LOW notifications (default 60).
    """

    def __init__(
        self,
        bot,
        room_id: str,
        *,
        priority_room_id: str | None = None,
        batch_window_sec: int = 60,
    ):
        self.bot = bot
        self.room_id = room_id
        self.priority_room_id = priority_room_id or room_id
        self._batch_window_sec = batch_window_sec

        self._running = False
        self._batch_buffer: list[Notification] = []
        self._batch_lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    # ── ChannelNotifier interface ──

    async def start(self) -> None:
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("Matrix notifier started (room=%s)", self.room_id)

    async def stop(self) -> None:
        self._running = False
        # Flush remaining
        await self._flush_batch()
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown
        logger.info("Matrix notifier stopped")

    async def send(self, notification: Notification) -> bool:
        """Queue or immediately send a notification.

        Immediate sends (CRITICAL/HIGH/NORMAL) return their real delivery result;
        batched LOW returns True ("accepted for delivery").
        """
        if notification.priority in (
            NotificationPriority.CRITICAL,
            NotificationPriority.HIGH,
        ):
            # Send immediately to priority room
            return await self._send_now(notification, self.priority_room_id)
        elif notification.priority == NotificationPriority.LOW:
            # Batch low-priority
            async with self._batch_lock:
                self._batch_buffer.append(notification)
            return True
        else:
            # NORMAL — send now to default room
            return await self._send_now(notification, self.room_id)

    async def send_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH,
    ) -> bool:
        notif = Notification(
            type="alert",
            title=title,
            message=message,
            priority=priority,
        )
        return await self.send(notif)

    # ── Internal helpers ──

    async def _send_now(self, notification: Notification, room_id: str) -> bool:
        """Send a single notification to a Matrix room. True only if it landed.

        This used to catch exceptions and otherwise return True — and its docstring
        claimed that closed the phantom-success hole. It did not. ``NavigMatrixBot``
        signals failure by **returning None**, not by raising: it has no client, the
        server answered with something other than a ``RoomSendResponse``, or it
        caught its own exception. So the `except` arm never fired for a failed send
        and every rejection was reported as delivered — including from a bot that
        never connected.
        """
        text = _format_for_matrix(notification)
        try:
            if notification.priority == NotificationPriority.CRITICAL:
                event_id = await self.bot.send_message(room_id, text)
            else:
                event_id = await self.bot.send_notice(room_id, text)
            if not event_id:
                logger.warning(
                    "Matrix notifier: NOT delivered to %s — the bot returned no event "
                    "id (not connected or rejected): %r",
                    room_id,
                    notification.title,
                )
                return False
            return True
        except Exception:
            logger.exception("Matrix notifier: failed to send to %s", room_id)
            return False

    async def _flush_batch(self) -> bool:
        """Flush batched LOW-priority notifications. True when delivered.

        The buffer is drained BEFORE the send, so a raise here used to lose every
        batched notification at once, leaving a log line as the only trace — the
        same drop the Telegram notifier's queue had. Batched LOW is the one
        priority whose ``send()`` already returned True ("accepted for
        delivery"), which makes the loss entirely invisible upstream.

        Rejected items go back for another window, on the shared attempt budget.
        """
        async with self._batch_lock:
            if not self._batch_buffer:
                return True
            items = self._batch_buffer.copy()
            self._batch_buffer.clear()

        # Combine into a single message
        parts = []
        for n in items:
            parts.append(f"• {n.title}: {n.message}")

        combined = f"\U0001f4e5 **Notifications** ({len(items)})\n\n" + "\n".join(parts)
        try:
            # None, not an exception, is how this transport reports a failed send —
            # so the retry machinery below was unreachable for the common failure.
            if not await self.bot.send_notice(self.room_id, combined):
                logger.warning(
                    "Matrix notifier: batch NOT delivered (%d items) — the bot "
                    "returned no event id",
                    len(items),
                )
                await self._requeue_batch(items)
                return False
        except Exception:
            logger.exception("Matrix notifier: batch flush failed")
            await self._requeue_batch(items)
            return False
        for n in items:
            n.delivery_attempts = 0
        return True

    async def _requeue_batch(self, items: list[Notification]) -> None:
        """Return a rejected batch to the buffer for the next flush window."""
        retry: list[Notification] = []
        for n in items:
            n.delivery_attempts += 1
            if n.delivery_attempts >= self._MAX_DELIVERY_ATTEMPTS:
                logger.error(
                    "Matrix notification DROPPED after %d failed attempts: %r [%s]",
                    n.delivery_attempts,
                    n.title,
                    n.type,
                )
            else:
                retry.append(n)
        if not retry:
            return
        async with self._batch_lock:
            # Preserve arrival order against anything buffered during the send.
            self._batch_buffer[:0] = retry

    async def _flush_loop(self) -> None:
        """Periodically flush the batch buffer."""
        while self._running:
            try:
                await asyncio.sleep(self._batch_window_sec)
                await self._flush_batch()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Matrix notifier: flush loop error")
                await asyncio.sleep(5)
