"""
plugins/quote.py — Saved quote system.
Save  : reply to any message with "quote" / "цитата" / "цитатка"
Fetch : /quote            — random from this chat
        /quote global      — random from all chats
        /quote stats       — per-user stats in this chat
        /quote @user       — random by that user
        /quote N @user     — N random quotes by that user
Admin : /quotes_disable @user  |  /quotes_enable @user
"""

from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_DB = Path.home() / ".navig" / "plugins" / "saved_quotes.db"
_DB.parent.mkdir(parents=True, exist_ok=True)

_KEYWORD = r"^(quote|цитата|цитатка|цтт)$"


def _db():
    c = sqlite3.connect(str(_DB))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(
        """CREATE TABLE IF NOT EXISTS quotes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT, author_id TEXT, author_name TEXT,
        text TEXT, saved_by TEXT,
        saved_at TEXT DEFAULT(datetime('now')))"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS quote_blacklist(
        chat_id TEXT, user_id TEXT, PRIMARY KEY(chat_id,user_id))"""
    )
    c.commit()
    return c


class QuotePlugin(BotPlugin):
    """Save chat messages as quotes; retrieve random quotes per chat or globally."""

    @property
    def meta(self):
        return PluginMeta(
            "quote",
            "Save chat quotes (reply 'quote') and retrieve with /quote.",
            "1.0.0",
        )

    @property
    def command(self):
        return "quote"

    @property
    def passive_patterns(self):
        return [_KEYWORD]

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        txt = " ".join(args).strip()
        cid = str(update.effective_chat.id)

        if txt.lower() == "stats":
            await self._stats(update, cid)
            return
        if txt.lower() in ("global", "all"):
            await self._random(update, None)
            return

        mn = re.match(r"^(\d+)\s+@?(\w+)$", txt, re.I)
        mu = re.match(r"^@?(\w+)$", txt)

        if mn:
            await self._by_user(update, cid, mn.group(2), int(mn.group(1)))
        elif mu and txt:
            await self._by_user(update, cid, mu.group(1), 1)
        else:
            await self._random(update, cid)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg or not msg.reply_to_message:
            return
        if not re.match(_KEYWORD, (msg.text or "").strip(), re.I):
            return
        cid = str(update.effective_chat.id)
        target = msg.reply_to_message
        author = target.from_user
        if not author or author.is_bot:
            await msg.reply_text("🚫 Cannot quote a bot.")
            return
        with _db() as c:
            if c.execute(
                "SELECT 1 FROM quote_blacklist WHERE chat_id=? AND user_id=?",
                (cid, str(author.id)),
            ).fetchone():
                await msg.reply_text("🚫 This user is on the quote blacklist.")
                return
        qtext = target.text or target.caption or ""
        if not qtext:
            await msg.reply_text("⚠️ That message has no text.")
            return
        aname = author.username or author.first_name or str(author.id)
        sname = (
            msg.from_user.username or msg.from_user.first_name or str(msg.from_user.id)
        )
        with _db() as c:
            c.execute(
                "INSERT INTO quotes(chat_id,author_id,author_name,text,saved_by) VALUES(?,?,?,?,?)",
                (cid, str(author.id), aname, qtext, sname),
            )
            c.commit()
        await msg.reply_text(
            f"💬 Quote by *{html.escape(aname)}* saved!", parse_mode="Markdown"
        )

    async def _random(self, update, chat_id: Optional[str]):
        with _db() as c:
            row = (
                c.execute(
                    "SELECT * FROM quotes WHERE chat_id=? ORDER BY RANDOM() LIMIT 1",
                    (chat_id,),
                ).fetchone()
                if chat_id
                else c.execute(
                    "SELECT * FROM quotes ORDER BY RANDOM() LIMIT 1"
                ).fetchone()
            )
        if not row:
            scope = "this chat" if chat_id else "any chat"
            await update.message.reply_text(
                f"📭 No quotes saved in {scope} yet.\nReply to any message with *quote* to save one!",
                parse_mode="Markdown",
            )
            return
        await update.message.reply_text(
            f'💬 _"{html.escape(row["text"])}"_\n\n— *{html.escape(row["author_name"])}*',
            parse_mode="Markdown",
        )

    async def _stats(self, update, chat_id):
        with _db() as c:
            rows = c.execute(
                "SELECT author_name,COUNT(*) n FROM quotes WHERE chat_id=? GROUP BY author_id ORDER BY n DESC LIMIT 10",
                (chat_id,),
            ).fetchall()
            total = c.execute(
                "SELECT COUNT(*) FROM quotes WHERE chat_id=?", (chat_id,)
            ).fetchone()[0]
        if not rows:
            await update.message.reply_text("📭 No quotes saved here yet.")
            return
        lines = [f"📊 *Quote Stats* — {total} total\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. *{html.escape(r['author_name'])}* — {r['n']}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _by_user(self, update, chat_id, username, count):
        with _db() as c:
            rows = c.execute(
                "SELECT * FROM quotes WHERE chat_id=? AND LOWER(author_name)=LOWER(?) ORDER BY RANDOM() LIMIT ?",
                (chat_id, username, min(count, 5)),
            ).fetchall()
        if not rows:
            await update.message.reply_text(
                f"📭 No quotes for @{html.escape(username)} here."
            )
            return
        for r in rows:
            await update.message.reply_text(
                f'💬 _"{html.escape(r["text"])}"_\n\n— *{html.escape(r["author_name"])}*',
                parse_mode="Markdown",
            )


def create():
    return QuotePlugin()
