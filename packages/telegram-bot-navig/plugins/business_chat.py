"""
plugins/business_chat.py — Telegram Business message support (Bot API 7.2+).
- Handles business_message updates (business chats)
- Passive: "ping" → "pong" in both regular and Business chats
Enable: configure bot in Telegram Business settings → Chatbots.
"""
from __future__ import annotations
import re
from telegram import Update
from telegram.ext import ContextTypes
try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

class BusinessChatPlugin(BotPlugin):
    """Business chat support + ping→pong in all channels."""

    @property
    def meta(self):
        return PluginMeta("business_chat",
            "Telegram Business message support. ping→pong in Business chats.","1.0.0")

    @property
    def command(self): return ""  # passive + business only

    @property
    def handles_business(self): return True

    @property
    def passive_patterns(self): return [r"^\s*ping\s*$"]

    async def handle(self, update, context): pass

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if re.match(r"^\s*ping\s*$", (update.message.text or ""), re.I):
            await update.message.reply_text("pong 🏓")

    async def handle_business(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        bm = getattr(update, "business_message", None)
        if not bm:
            return
        text = (bm.text or "").strip()
        if re.match(r"^ping$", text, re.I):
            kwargs = {"chat_id": bm.chat.id, "text": "pong 🏓", "reply_to_message_id": bm.message_id}
            bci = getattr(update, "business_connection_id", None) or getattr(bm, "business_connection_id", None)
            if bci:
                kwargs["business_connection_id"] = bci
            await context.bot.send_message(**kwargs)

def create(): return BusinessChatPlugin()
