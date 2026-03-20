"""
plugins/music.py — Music URL cross-platform conversion (song.link / Odesli API).
Command : /music <url>
Passive : Auto-detect Spotify/Apple Music/YouTube Music/Deezer/Tidal/SoundCloud/Amazon URLs.
API     : https://api.song.link/v1-alpha.1/links (free, no key)
"""
from __future__ import annotations
import asyncio, json, re, urllib.parse, urllib.request
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes
try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_MUSIC_RE = (
    r"https?://(?:open\.spotify\.com|music\.apple\.com|music\.youtube\.com"
    r"|(?:www\.)?deezer\.com|tidal\.com|soundcloud\.com"
    r"|music\.amazon\.com|music\.amazon\.co\.uk|www\.last\.fm"
    r"|(?:[\w-]+\.)?bandcamp\.com|listen\.tidal\.com|napster\.com)\S+"
)

_LABELS: dict[str, str] = {
    "spotify":"🟢 Spotify","itunes":"🍎 Apple Music","youtubeMusic":"🔴 YouTube Music",
    "deezer":"🟣 Deezer","tidal":"⚫ Tidal","soundcloud":"🟠 SoundCloud",
    "amazonMusic":"🔵 Amazon Music","amazonStore":"📦 Amazon Store","youtube":"▶️ YouTube",
    "pandora":"🎵 Pandora","napster":"🎶 Napster","yandex":"🟡 Yandex Music",
    "bandcamp":"🎸 Bandcamp","anghami":"🎵 Anghami","boomplay":"🎵 Boomplay",
    "audius":"🎼 Audius","spinrilla":"💿 Spinrilla",
}

class MusicPlugin(BotPlugin):
    """Convert music links to all 19+ platforms via song.link."""

    @property
    def meta(self):
        return PluginMeta("music","Convert music links across 19+ platforms via song.link.","1.0.0")

    @property
    def command(self): return "music"

    @property
    def passive_patterns(self): return [_MUSIC_RE]

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "🎵 *Music Link Converter*\n\nUsage: `/music <url>`\n\n"
                "Or just paste any Spotify / Apple Music / Deezer / Tidal / SoundCloud URL — "
                "I will convert it to links for all major platforms automatically.",
                parse_mode="Markdown")
            return
        await self._process(update, args[0])

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        urls = re.findall(_MUSIC_RE, update.message.text or "")
        if urls:
            await self._process(update, urls[0])

    async def _process(self, update, url):
        status = await update.message.reply_text("🎵 Fetching platform links…")
        data = await asyncio.to_thread(self._odesli, url)
        if not data:
            await status.edit_text("❌ Track not found on song.link.")
            return
        await status.edit_text(self._fmt(data), parse_mode="Markdown", disable_web_page_preview=True)

    @staticmethod
    def _odesli(url) -> dict[str,Any] | None:
        enc = urllib.parse.quote(url, safe="")
        try:
            with urllib.request.urlopen(
                f"https://api.song.link/v1-alpha.1/links?url={enc}&userCountry=US",
                timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            return None

    @staticmethod
    def _fmt(data) -> str:
        eid = data.get("entityUniqueId","")
        ents = data.get("entitiesByUniqueId",{})
        title = "🎵 Track Links"
        if eid and eid in ents:
            e = ents[eid]
            a, t = e.get("artistName",""), e.get("title","")
            if a and t: title = f"🎵 *{t}*\nby _{a}_"
        lines = [title, ""]
        for plat, info in data.get("linksByPlatform",{}).items():
            label = _LABELS.get(plat, f"🎵 {plat.capitalize()}")
            link  = info.get("url","")
            if link: lines.append(f"[{label}]({link})")
        return "\n".join(lines) if len(lines)>2 else "❌ No platform links found."

def create(): return MusicPlugin()
