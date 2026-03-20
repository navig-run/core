"""
plugins/ping.py — Health-check ping.

Command : /ping
Effect  : Replies with "Pong! 🏓" and the current bot latency in ms.
"""

from __future__ import annotations

import time

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore[no-redef]


class PingPlugin(BotPlugin):
    """Health-check ping — reply with Pong and current latency."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="ping",
            description="Health check — replies with Pong and bot latency.",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        return "ping"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        start = time.monotonic()
        msg = await update.message.reply_text("Pong! 🏓")
        elapsed_ms = (time.monotonic() - start) * 1000
        await msg.edit_text(f"Pong! 🏓  _{elapsed_ms:.0f} ms_", parse_mode="Markdown")


def create() -> PingPlugin:
    return PingPlugin()
