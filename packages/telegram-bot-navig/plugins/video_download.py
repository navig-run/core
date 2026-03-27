"""
plugins/video_download.py — Auto-detect and download videos.
Platforms: TikTok, Instagram, YouTube, Twitter/X, Snapchat, Facebook, Reddit.
  ≤50 MB   → send via sendVideo
  ≤200 MB  → upload to Catbox.moe, send link
  ≤500 MB  → upload to GoFile.io, send link
Requires: yt-dlp (pip install yt-dlp)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

logger = logging.getLogger(__name__)

_VIDEO_RE = (
    r"https?://(?:www\.)?"
    r"(?:tiktok\.com/[@\w./]+|vm\.tiktok\.com/\w+"
    r"|instagram\.com/(?:p|reel|tv)/[\w-]+"
    r"|youtu\.be/[\w-]+|youtube\.com/(?:watch\?v=|shorts/)[\w-]+"
    r"|(?:twitter|x)\.com/\w+/status/\d+"
    r"|snapchat\.com/[\w./]+"
    r"|(?:www\.)?facebook\.com/\S+/videos/\d+|fb\.watch/\w+"
    r"|reddit\.com/r/\w+/comments/\w+|v\.redd\.it/\w+)\S*"
)
_50MB = 50 * 1024 * 1024
_200MB = 200 * 1024 * 1024


class VideoDownloadPlugin(BotPlugin):
    """Auto-download videos from TikTok/Instagram/YouTube/Twitter/Snapchat/Facebook/Reddit."""

    @property
    def meta(self):
        return PluginMeta(
            "video_download",
            "Auto-detect & download videos from TikTok/Instagram/YouTube/Twitter/Reddit.",
            "1.0.0",
        )

    @property
    def command(self):
        return ""  # passive only

    @property
    def passive_patterns(self):
        return [_VIDEO_RE]

    async def handle(self, update, context):
        pass

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text or ""
        urls = re.findall(_VIDEO_RE, text)
        if not urls:
            return
        if not shutil.which("yt-dlp"):
            await update.message.reply_text(
                "⚠️ `yt-dlp` not found. Install with `pip install yt-dlp`.",
                parse_mode="Markdown",
            )
            return
        for url in urls[:3]:
            await self._process(update, context, url)

    async def _process(self, update, context, url):
        status = await update.message.reply_text("⏳ Downloading…")
        tmp = tempfile.mkdtemp(prefix="navig_vid_")
        try:
            path = await asyncio.to_thread(self._dl, url, tmp)
            if not path:
                await status.edit_text("❌ Download failed (private or unavailable).")
                return
            size = Path(path).stat().st_size
            if size <= _50MB:
                await status.edit_text("📤 Sending…")
                with open(path, "rb") as f:
                    await context.bot.send_video(
                        update.effective_chat.id,
                        video=f,
                        caption=f"🎬 {Path(path).stem[:100]}",
                        supports_streaming=True,
                    )
                await status.delete()
            elif size <= _200MB:
                await status.edit_text(
                    "📤 Too large for Telegram → uploading to Catbox…"
                )
                link = await asyncio.to_thread(self._catbox, path)
                await status.edit_text(
                    (
                        f"🎬 [Watch / Download]({link})"
                        if link
                        else "❌ Catbox upload failed."
                    ),
                    parse_mode="Markdown",
                )
            else:
                await status.edit_text("📤 Large file → uploading to GoFile…")
                link = await asyncio.to_thread(self._gofile, path)
                await status.edit_text(
                    (
                        f"🎬 [Watch / Download]({link})"
                        if link
                        else "❌ GoFile upload failed."
                    ),
                    parse_mode="Markdown",
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _dl(url, outdir):
        try:
            r = subprocess.run(
                [
                    "yt-dlp",
                    "--no-playlist",
                    "--max-filesize",
                    "500m",
                    "--merge-output-format",
                    "mp4",
                    "-o",
                    os.path.join(outdir, "%(title).80s.%(ext)s"),
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if r.returncode != 0:
                logger.warning("yt-dlp: %s", r.stderr[:300])
                return None
            files = list(Path(outdir).iterdir())
            return str(files[0]) if files else None
        except Exception as e:
            logger.warning("yt-dlp failed: %s", e)
            return None

    @staticmethod
    def _catbox(filepath):
        try:
            import mimetypes

            ct, _ = mimetypes.guess_type(filepath)
            fn = Path(filepath).name
            b = "----NavCatbox"
            with open(filepath, "rb") as f:
                data = f.read()
            body = (
                (
                    f'--{b}\r\nContent-Disposition: form-data; name="reqtype"\r\n\r\nfileupload\r\n'
                    f'--{b}\r\nContent-Disposition: form-data; name="fileToUpload"; filename="{fn}"\r\n'
                    f"Content-Type: {ct or 'application/octet-stream'}\r\n\r\n"
                ).encode()
                + data
                + f"\r\n--{b}--\r\n".encode()
            )
            req = urllib.request.Request(
                "https://catbox.moe/user/api.php",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={b}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                url = resp.read().decode().strip()
                return url if url.startswith("https://") else None
        except Exception as e:
            logger.warning("Catbox: %s", e)
            return None

    @staticmethod
    def _gofile(filepath):
        try:
            with urllib.request.urlopen(
                "https://api.gofile.io/servers", timeout=10
            ) as r:
                srv = json.loads(r.read())["data"]["servers"][0]["name"]
            fn = Path(filepath).name
            b = "----NavGoFile"
            with open(filepath, "rb") as f:
                data = f.read()
            body = (
                (
                    f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fn}"\r\n'
                    f"Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
                + data
                + f"\r\n--{b}--\r\n".encode()
            )
            req = urllib.request.Request(
                f"https://{srv}.gofile.io/uploadFile",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={b}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read())
            return (
                res.get("data", {}).get("downloadPage")
                if res.get("status") == "ok"
                else None
            )
        except Exception as e:
            logger.warning("GoFile: %s", e)
            return None


def create():
    return VideoDownloadPlugin()
