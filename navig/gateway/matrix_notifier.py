"""
MatrixNotifier — ChannelNotifier implementation for Matrix.

Bridges the NAVIG notification pipeline to Matrix rooms.
Supports batching, quiet hours, scheduled briefings, and priority routing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from navig.gateway.notifications import (
    ChannelNotifier,
    Notification,
    NotificationPriority,
)

logger = logging.getLogger(__name__)


def _format_for_matrix(notification: Notification) -> str:
    """Format a Notification for Matrix (plain text with unicode icons)."""
    emoji_map = {
        "alert": "\u26a0\ufe0f",       # ⚠️
        "briefing": "\U0001f4ca",       # 📊
        "routine": "\u2600\ufe0f",      # ☀️
        "heartbeat": "\U0001f493",      # 💓
        "reminder": "\u23f0",           # ⏰
    }
    priority_prefix = {
        NotificationPriority.CRITICAL: "\U0001f534 ",  # 🔴
        NotificationPriority.HIGH: "\U0001f7e1 ",       # 🟡
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
        priority_room_id: Optional[str] = None,
        batch_window_sec: int = 60,
    ):
        self.bot = bot
        self.room_id = room_id
        self.priority_room_id = priority_room_id or room_id
        self._batch_window_sec = batch_window_sec

        self._running = False
        self._batch_buffer: List[Notification] = []
        self._batch_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None

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
                pass
        logger.info("Matrix notifier stopped")

    async def send(self, notification: Notification) -> None:
        """Queue or immediately send a notification."""
        if notification.priority in (
            NotificationPriority.CRITICAL,
            NotificationPriority.HIGH,
        ):
            # Send immediately to priority room
            await self._send_now(notification, self.priority_room_id)
        elif notification.priority == NotificationPriority.LOW:
            # Batch low-priority
            async with self._batch_lock:
                self._batch_buffer.append(notification)
        else:
            # NORMAL — send now to default room
            await self._send_now(notification, self.room_id)

    async def send_alert(
        self,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.HIGH,
    ) -> None:
        notif = Notification(
            type="alert",
            title=title,
            message=message,
            priority=priority,
        )
        await self.send(notif)

    # ── Internal helpers ──

    async def _send_now(self, notification: Notification, room_id: str) -> None:
        """Send a single notification to a Matrix room."""
        text = _format_for_matrix(notification)
        try:
            if notification.priority == NotificationPriority.CRITICAL:
                await self.bot.send_message(room_id, text)
            else:
                await self.bot.send_notice(room_id, text)
        except Exception:
            logger.exception("Matrix notifier: failed to send to %s", room_id)

    async def _flush_batch(self) -> None:
        """Flush batched LOW-priority notifications."""
        async with self._batch_lock:
            if not self._batch_buffer:
                return
            items = self._batch_buffer.copy()
            self._batch_buffer.clear()

        if not items:
            return

        # Combine into a single message
        parts = []
        for n in items:
            parts.append(f"• {n.title}: {n.message}")

        combined = (
            f"\U0001f4e5 **Notifications** ({len(items)})\n\n"
            + "\n".join(parts)
        )
        try:
            await self.bot.send_notice(self.room_id, combined)
        except Exception:
            logger.exception("Matrix notifier: batch flush failed")

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
