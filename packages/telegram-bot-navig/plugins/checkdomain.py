"""
plugins/checkdomain.py — Domain availability checker (RDAP, no API key).
Command : /checkdomain <domain>
Passive : "check domain example.com" / "is navig.io available" / "check if X is taken"
"""
from __future__ import annotations
import asyncio, re, urllib.error, urllib.request
from telegram import Update
from telegram.ext import ContextTypes
try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_DOM = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+")
_NL  = r"(?:check\s+(?:domain|if)?\s*|is\s+)([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})(?:\s+(?:available|taken|registered|free))?|([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})\s+available"

class CheckDomainPlugin(BotPlugin):
    """Check whether a domain is available via RDAP (free, no API key)."""

    @property
    def meta(self):
        return PluginMeta("checkdomain","Check domain availability: /checkdomain example.com","1.0.0")

    @property
    def command(self): return "checkdomain"

    @property
    def passive_patterns(self): return [_NL]

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "🌐 *Domain Checker*\n\nUsage: `/checkdomain example.com`\n\n"
                "Or say:\n• _check domain navig.io_\n• _is schema.cx available_",
                parse_mode="Markdown"); return
        await self._check(update, args[0].lower().strip("./"))

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        m = re.search(_NL, update.message.text or "", re.I)
        if m:
            dom = (m.group(1) or m.group(2) or "").lower().strip("./")
            if dom: await self._check(update, dom)

    async def _check(self, update, domain):
        if not _DOM.fullmatch(domain):
            await update.message.reply_text(f"⚠️ `{domain}` is not a valid domain.", parse_mode="Markdown"); return
        status = await update.message.reply_text(f"🔍 Checking `{domain}`…", parse_mode="Markdown")
        result = await asyncio.to_thread(self._rdap, domain)
        text = {
            "available": f"✅ *{domain}* appears to be *available!*",
            "taken":     f"❌ *{domain}* is already registered.",
        }.get(result, f"⚠️ Could not determine availability of *{domain}*.")
        await status.edit_text(text, parse_mode="Markdown")

    @staticmethod
    def _rdap(domain):
        try:
            req = urllib.request.Request(f"https://rdap.org/domain/{domain}",
                headers={"Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200: return "taken"
            return "unknown"
        except urllib.error.HTTPError as e:
            return "available" if e.code == 404 else "unknown"
        except Exception:
            return "unknown"

def create(): return CheckDomainPlugin()
