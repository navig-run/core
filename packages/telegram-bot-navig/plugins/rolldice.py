"""
plugins/rolldice.py — Reference plugin implementation.

Command : /rolldice
Effect  : Rolls a fair six-sided die and replies with the result.
"""

from __future__ import annotations

import random

from telegram import Update
from telegram.ext import ContextTypes

# Use a relative import when running from the package root, or a direct import
# when the loader has added the parent directory to sys.path.
try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore[no-redef]


class RollDicePlugin(BotPlugin):
    """Roll a six-sided die on demand."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="rolldice",
            description="Roll a six-sided die and return the result.",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        return "rolldice"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        result = random.randint(1, 6)
        faces = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
        await update.message.reply_text(
            f"{faces[result]} You rolled a *{result}*!", parse_mode="Markdown"
        )


# Required factory — called by PluginLoader
def create() -> RollDicePlugin:
    return RollDicePlugin()
