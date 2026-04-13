"""
plugins/flip.py — Flip a coin.

Command : /flip
Effect  : Returns Heads or Tails with equal probability.
"""

from __future__ import annotations

import random

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore[no-redef]


class FlipPlugin(BotPlugin):
    """Flip a coin."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="flip",
            description="Flip a coin — Heads or Tails.",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        return "flip"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        result = random.choice([("🪙 <b>Heads!</b>", "heads"), ("🪙 <b>Tails!</b>", "tails")])
        await update.message.reply_text(result[0], parse_mode="HTML")


def create() -> FlipPlugin:
    return FlipPlugin()
