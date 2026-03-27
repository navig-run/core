"""
Matrix Channel Adapter for NAVIG Gateway

Bridges the gateway ChannelAdapter interface to the existing NavigMatrixBot.
Uses matrix-nio via the comms.matrix module.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class MatrixChannelAdapter:
    """
    Gateway channel adapter for Matrix protocol.

    Wraps navig.comms.matrix.NavigMatrixBot so the channel registry
    can interact with Matrix via the standard adapter interface.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._bot = None
        self._config = config or {}

    def _ensure_bot(self):
        """Lazy-load and configure the NavigMatrixBot."""
        if self._bot is not None:
            return
        try:
            from navig.comms.matrix import NavigMatrixBot, get_matrix_bot

            # Try the running singleton first
            existing = get_matrix_bot()
            if existing and existing.is_running:
                self._bot = existing
                return

            # Create new from config
            if self._config:
                self._bot = NavigMatrixBot(self._config)
        except ImportError:
            logger.error("matrix-nio is not installed. pip install matrix-nio[e2e]")

    async def start(self) -> None:
        """Start the Matrix bot (login + sync)."""
        self._ensure_bot()
        if self._bot and not self._bot.is_running:
            await self._bot.start()

    async def stop(self) -> None:
        """Stop the Matrix bot."""
        if self._bot and self._bot.is_running:
            await self._bot.stop()

    async def send(self, target: str, message: str, **kwargs) -> str | None:
        """
        Send a text message to a Matrix room.

        Args:
            target: Room ID or alias (e.g. '!abc:navig.local')
            message: Message text
            **kwargs: Extra options (notice=True for m.notice)

        Returns:
            Event ID string or None on failure
        """
        self._ensure_bot()
        if not self._bot:
            return None

        if kwargs.get("notice"):
            return await self._bot.send_notice(target, message)
        return await self._bot.send_message(target, message)

    async def on_message(self, callback: Callable) -> None:
        """Register a message callback: async fn(room_id, sender, body)."""
        self._ensure_bot()
        if self._bot:
            self._bot.on_message(callback)

    @property
    def is_connected(self) -> bool:
        """Check if the Matrix bot is running."""
        return self._bot is not None and self._bot.is_running

    async def get_rooms(self) -> list[dict[str, Any]]:
        """List joined rooms via the bot."""
        self._ensure_bot()
        if not self._bot:
            return []
        return await self._bot.get_rooms()

    async def get_room_messages(
        self, room_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent messages from a room."""
        self._ensure_bot()
        if not self._bot:
            return []
        return await self._bot.get_room_messages(room_id, limit=limit)
