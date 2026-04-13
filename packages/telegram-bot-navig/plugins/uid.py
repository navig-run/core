"""plugins/uid.py — Show a user's Telegram ID. /uid @user or reply."""

from __future__ import annotations

import html

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore


class UidPlugin(BotPlugin):
    @property
    def meta(self):
        return PluginMeta("uid", "Show Telegram user ID: /uid @user or reply", "1.0.0")

    @property
    def command(self):
        return "uid"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []

        async def _get(ident):
            try:
                return await context.bot.get_chat(ident)
            except TelegramError:
                return None

        if args:
            c = await _get(args[0])
            if c:
                await update.message.reply_text(
                    f"🆔 <b>{html.escape(c.full_name or str(c.id))}</b>\n<code>{c.id}</code>",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text("❌ User not found.")
        elif (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
        ):
            u = update.message.reply_to_message.from_user
            await update.message.reply_text(
                f"🆔 <b>{html.escape(u.full_name)}</b>\n<code>{u.id}</code>", parse_mode="HTML"
            )
        else:
            u = update.effective_user
            await update.message.reply_text(
                f"🆔 <b>Your Telegram ID</b>\n<code>{u.id}</code>", parse_mode="HTML"
            )


def create():
    return UidPlugin()
