"""
plugins/profile.py — Show Telegram user profile as a NAVIG node card.
Command : /profile [username|@user|user_id]
          Reply to a message to profile that user.
"""

from __future__ import annotations

import html

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore


async def _get(context, ident):
    try:
        return await context.bot.get_chat(ident)
    except TelegramError:
        return None


class ProfilePlugin(BotPlugin):
    """Show a Telegram user's info as a NAVIG node card."""

    @property
    def meta(self):
        return PluginMeta(
            "profile", "Show Telegram user profile: /profile @user", "1.0.0"
        )

    @property
    def command(self):
        return "profile"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        chat = None
        if args:
            chat = await _get(context, args[0])
        elif update.message.reply_to_message:
            u = update.message.reply_to_message.from_user
            if u:
                chat = await _get(context, str(u.id))
        else:
            u = update.effective_user
            if u:
                chat = await _get(context, str(u.id))
        if not chat:
            await update.message.reply_text("❌ User not found.")
            return
        name = html.escape(chat.full_name or chat.title or str(chat.id))
        lines = [f"🔵 <b>NAVIG Node — {name}</b>\n", f"🆔 ID: <code>{chat.id}</code>"]
        if getattr(chat, "username", None):
            lines.append(f"🔗 @{chat.username}")
        if getattr(chat, "first_name", None):
            lines.append(
                f"📛 {html.escape(chat.first_name)}"
                + (
                    f" {html.escape(chat.last_name)}"
                    if getattr(chat, "last_name", None)
                    else ""
                )
            )
        if getattr(chat, "bio", None):
            lines.append(f"\n📝 <i>{html.escape(chat.bio)}</i>")
        if getattr(chat, "type", None):
            lines.append(f"📂 Type: {chat.type}")
        if getattr(chat, "is_bot", False):
            lines.append("🤖 Bot: ✅")
        if getattr(chat, "is_premium", False):
            lines.append("⭐ Premium: ✅")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def create():
    return ProfilePlugin()
