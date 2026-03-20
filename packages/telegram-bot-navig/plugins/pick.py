"""
plugins/pick.py — Pick randomly from options.
Command : /pick a, b, c
Passive : "pick between coffee or tea" / "choose between A and B" / "выбери X или Y"
"""
from __future__ import annotations
import random, re
from telegram import Update
from telegram.ext import ContextTypes
try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_NL = r"(?:pick|choose|выбери|выбор|decide|décide)\s+(?:between\s+)?(.+)"

class PickPlugin(BotPlugin):
    @property
    def meta(self): return PluginMeta("pick","Pick randomly from options: /pick pizza, sushi, tacos","1.0.0")
    @property
    def command(self): return "pick"
    @property
    def passive_patterns(self): return [_NL]

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._pick(update, " ".join(context.args or []))

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        m = re.search(_NL, update.message.text or "", re.I)
        if m: await self._pick(update, m.group(1))

    async def _pick(self, update, raw: str):
        items = [i.strip() for i in re.split(r"[,]|\bor\b|\band\b|\bили\b|\bou\b", raw, flags=re.I) if i.strip()]
        if len(items) < 2:
            await update.message.reply_text(
                "🎯 Usage: `/pick pizza, sushi, tacos`\nOr say: _choose between coffee or tea_",
                parse_mode="Markdown"); return
        await update.message.reply_text(f"🎯 I choose: *{random.choice(items)}*", parse_mode="Markdown")

def create(): return PickPlugin()
