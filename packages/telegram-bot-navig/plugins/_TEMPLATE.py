"""
_TEMPLATE.py — Plugin template for telegram-bot-navig.

Copy this file, rename it (without the leading underscore), and fill in
the marked sections.  Files starting with `_` are skipped by the loader.

Quick-start
-----------
1.  Copy this file:   cp _TEMPLATE.py myplugin.py
2.  Create a sidecar: cp _TEMPLATE.json myplugin.json   (declare pip deps there)
3.  Fill in the four marked TODO sections below.
4.  Drop it into `plugins/`.  If hot-reload is on, it loads immediately.
5.  Run tests:  py -3 -m pytest tests/ --no-cov -q

Sidecar format (myplugin.json):
--------------------------------
{
  "id": "myplugin",
  "version": "1.0.0",
  "description": "One-line description.",
  "provides": [],      // optional capability tags, e.g. ["bot.greeting"]
  "depends": {
    "pip": []          // e.g. ["requests>=2.31.0", "beautifulsoup4>=4.12"]
  }
}

Provides conflict detection
----------------------------
If two plugins declare the same capability in `provides`, the second one to
load is skipped with a conflict error visible in /plugins.  Use this to
prevent two plugins from owning the same command or service.
"""

from __future__ import annotations

# TODO 1 — import anything you need
# from telegram import Update
# from telegram.ext import ContextTypes
import logging

from plugin_base import BotPlugin, PluginContext, PluginEvent, PluginMeta
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class MyPlugin(BotPlugin):
    """
    TODO 2 — rename this class and fill in the plugin metadata.
    """

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="myplugin",  # unique, lowercase, used for /activate
            description="Does something useful.",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        # TODO 3 — the slash command (without the slash), e.g. "hello"
        # Return "" if this is a passive-only or business-only plugin.
        return "myplugin"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # TODO 4 — implement your command logic here
        await update.message.reply_text("Hello from MyPlugin!")

    # ------------------------------------------------------------------ #
    # Optional: passive pattern matching (listens to all text messages)   #
    # ------------------------------------------------------------------ #

    # @property
    # def passive_patterns(self) -> list[str]:
    #     """Regex patterns.  If any matches the message text, handle_message fires."""
    #     return [r"https?://\S+"]
    #
    # async def handle_message(
    #     self, update: Update, context: ContextTypes.DEFAULT_TYPE
    # ) -> None:
    #     text = update.message.text or ""
    #     await update.message.reply_text(f"Detected a URL in: {text[:60]}")

    # ------------------------------------------------------------------ #
    # Optional: Telegram Business message handling                        #
    # ------------------------------------------------------------------ #

    # @property
    # def handles_business(self) -> bool:
    #     return True
    #
    # async def handle_business(
    #     self, update: Update, context: ContextTypes.DEFAULT_TYPE
    # ) -> None:
    #     msg = update.business_message
    #     await context.bot.send_message(
    #         chat_id=msg.chat.id,
    #         text="Got your business message!",
    #     )


# ---------------------------------------------------------------------------
# Required factory — always name this function `create`
# ---------------------------------------------------------------------------


def create() -> MyPlugin:
    """Return a fresh plugin instance.  Called once at bot startup (or hot-reload)."""
    return MyPlugin()
