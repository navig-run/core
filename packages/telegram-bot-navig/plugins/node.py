"""plugins/node.py — NAVIG node card alias (/node = /profile + NAVIG framing)."""

from __future__ import annotations

import html

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore


class NodePlugin(BotPlugin):
    @property
    def meta(self):
        return PluginMeta(
            "node", "Show user as a NAVIG node (alias for /profile)", "1.0.0"
        )

    @property
    def command(self):
        return "node"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        chat = None

        async def _get(ident):
            try:
                return await context.bot.get_chat(ident)
            except TelegramError:
                return None

        if args:
            chat = await _get(args[0])
        elif update.message.reply_to_message:
            u = update.message.reply_to_message.from_user
            if u:
                chat = await _get(str(u.id))
        else:
            u = update.effective_user
            if u:
                chat = await _get(str(u.id))
        if not chat:
            await update.message.reply_text("❌ Node not found.")
            return
        name = html.escape(chat.full_name or chat.title or str(chat.id))
        lines = ["🔵 *NAVIG Node*", "", f"👤 *{name}*", f"🆔 `{chat.id}`"]
        if getattr(chat, "username", None):
            lines.append(f"🔗 @{chat.username}")
        if getattr(chat, "bio", None):
            lines += ["", f"📝 _{html.escape(chat.bio)}_"]
        if getattr(chat, "is_bot", False):
            lines.append("🤖 Bot node")
        if getattr(chat, "is_premium", False):
            lines.append("⭐ Premium node")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def create():
    return NodePlugin()
