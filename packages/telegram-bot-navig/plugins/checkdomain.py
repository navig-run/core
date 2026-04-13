"""
plugins/checkdomain.py — Domain availability checker (RDAP, no API key).
Command : /checkdomain <domain>
Passive : "check domain example.com" / "is navig.io available" / "check if X is taken"
"""

from __future__ import annotations

import asyncio
import re
import urllib.error
import urllib.request

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_DOM = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+")
_NL = r"(?:check\s+(?:domain|if)?\s*|is\s+)([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})(?:\s+(?:available|taken|registered|free))?|([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})\s+available"


class CheckDomainPlugin(BotPlugin):
    """Check whether a domain is available via RDAP (free, no API key)."""

    @property
    def meta(self):
        return PluginMeta(
            "checkdomain",
            "Check domain availability: /checkdomain example.com",
            "1.0.0",
        )

    @property
    def command(self):
        return "checkdomain"

    @property
    def passive_patterns(self):
        return [_NL]

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "🌐 <b>Domain Checker</b>\n\nUsage: <code>/checkdomain example.com</code>\n\n"
                "Or say:\n• <i>check domain navig.io</i>\n• <i>is schema.cx available</i>",
                parse_mode="HTML",
            )
            return
        await self._check(update, args[0].lower().strip("./"))

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        m = re.search(_NL, update.message.text or "", re.I)
        if m:
            dom = (m.group(1) or m.group(2) or "").lower().strip("./")
            if dom:
                await self._check(update, dom)

    async def _check(self, update, domain):
        if not _DOM.fullmatch(domain):
            await update.message.reply_text(
                f"⚠️ <code>{domain}</code> is not a valid domain.", parse_mode="HTML"
            )
            return
        status = await update.message.reply_text(
            f"🔍 Checking <code>{domain}</code>…", parse_mode="HTML"
        )
        result = await asyncio.to_thread(self._rdap, domain)
        text = {
            "available": f"✅ <b>{domain}</b> appears to be <b>available!</b>",
            "taken": f"❌ <b>{domain}</b> is already registered.",
        }.get(result, f"⚠️ Could not determine availability of <b>{domain}</b>.")
        await status.edit_text(text, parse_mode="HTML")

    @staticmethod
    def _rdap(domain):
        try:
            req = urllib.request.Request(
                f"https://rdap.org/domain/{domain}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    return "taken"
            return "unknown"
        except urllib.error.HTTPError as e:
            return "available" if e.code == 404 else "unknown"
        except Exception:
            return "unknown"


def create():
    return CheckDomainPlugin()
