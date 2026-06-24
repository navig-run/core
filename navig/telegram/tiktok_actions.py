"""Bot-side TikTok actions: detect links, offer a card + buttons, analyse/download.

Wires :mod:`navig.tiktok.engine` into the Telegram bot + business layer. Every
action is gated by the owner's ``download`` per-tool policy (owner|both|off, see
:mod:`navig.telegram.permissions`) — a counterparty can only trigger it when the
owner allows. The AI briefing is the same no-tools, text-in/text-out call used by
the rest of the business layer, so a malicious description/comment can't escalate.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os

from navig.tiktok import engine

from . import permissions

logger = logging.getLogger(__name__)

# Reaction emojis that trigger a TikTok analysis (owner-remappable via config
# telegram.business.emoji.<emoji> = tiktok). Kept separate from the canned/AI
# reaction tables so each concern stays independent.
TIKTOK_REACTION_EMOJIS: frozenset[str] = frozenset({"🎵", "🎬", "📹"})

# Telegram bot-API upload ceiling for sendVideo (~50 MB).
_MAX_UPLOAD = 49_000_000


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore

    return TelegramCatalogStore()


def _is_owner(channel, user_id) -> bool:
    try:
        return int(user_id) in {int(x) for x in getattr(channel, "allowed_users", set())}
    except Exception:  # noqa: BLE001
        return False


def _url_from_ref(chat_id, message_id) -> str | None:
    """Re-extract the TikTok URL from a cataloged message (callbacks carry no body)."""
    try:
        row = _store().get_message_by_ref(int(chat_id), int(message_id))
        return engine.extract_url((row or {}).get("text") or "")
    except Exception:  # noqa: BLE001
        return None


# ── proactive card + buttons ──────────────────────────────────────────────────

async def offer_card(channel, chat_id: int, message_id: int, text: str, *,
                     is_owner: bool) -> bool:
    """If *text* has a TikTok link, reply with a metadata card + Download/Analyse
    buttons. Gated by the ``download`` policy. Returns True if a card was sent."""
    url = engine.extract_url(text)
    if not url:
        return False
    if not permissions.can_use("download", is_owner=is_owner):
        return False
    card = "🎵 <b>TikTok link</b>"
    try:
        meta = await asyncio.to_thread(engine.info, url)
        card = engine.render_card(meta)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok card metadata failed: %s", exc)
    keyboard = [[
        {"text": "⬇️ Download", "callback_data": f"tk:dl:{chat_id}:{message_id}"},
        {"text": "🔍 Analyse", "callback_data": f"tk:an:{chat_id}:{message_id}"},
    ]]
    try:
        await channel.send_message(
            chat_id, card, parse_mode="HTML",
            keyboard=keyboard, reply_to_message_id=message_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok offer_card send failed: %s", exc)
        return False


# ── callback (button) + reaction entry points ─────────────────────────────────

async def handle_callback(channel, cb_data: str, chat_id: int, message_id: int,
                          user_id: int) -> None:
    """Route ``tk:<action>:<src_chat>:<src_msg>`` button callbacks."""
    parts = cb_data.split(":")
    if len(parts) < 4:
        return
    action, src_chat, src_msg = parts[1], parts[2], parts[3]
    if not permissions.can_use("download", is_owner=_is_owner(channel, user_id)):
        await channel.send_message(chat_id, "⛔ Not permitted.", parse_mode=None)
        return
    url = _url_from_ref(src_chat, src_msg)
    if not url:
        await channel.send_message(chat_id, "Couldn't find the TikTok link.", parse_mode=None)
        return
    if action == "an":
        await _do_analyse(channel, chat_id, url)
    elif action == "dl":
        await _do_download(channel, chat_id, url)


async def handle_reaction(channel, chat_id: int, msg_id: int, user_id: int,
                          emoji: str) -> bool:
    """A TikTok emoji reaction on a message with a TikTok link → analyse it."""
    if not permissions.can_use("download", is_owner=_is_owner(channel, user_id)):
        return False
    url = _url_from_ref(chat_id, msg_id)
    if not url:
        return False
    await _do_analyse(channel, chat_id, url)
    return True


# ── workers ───────────────────────────────────────────────────────────────────

async def _do_analyse(channel, chat_id: int, url: str) -> None:
    try:
        result = await engine.analyse(url)
    except engine.TikTokUnavailable:
        await channel.send_message(chat_id, "TikTok engine unavailable — `pip install rapidok`.", parse_mode=None)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok analyse failed: %s", exc)
        await channel.send_message(chat_id, "Couldn't analyse that video.", parse_mode=None)
        return
    meta = result["meta"]
    brief = result["brief"] or _fallback_brief(meta)
    # The brief is markdown (headings/bullets/quotes) — send it as a RICH message so
    # Telegram renders it natively; send_rich_message falls back to HTML if needed.
    await channel.send_rich_message(chat_id, markdown=_brief_markdown(meta, brief))


async def _do_download(channel, chat_id: int, url: str) -> None:
    path = None
    try:
        path = await engine.fetch_file_async(url)
        size = os.path.getsize(path)
        if size > _MAX_UPLOAD:
            await channel.send_message(
                chat_id,
                f"⬇️ Downloaded ({size // 1_000_000} MB) — too large to upload here. Saved to <code>{_html.escape(path)}</code>.",
                parse_mode="HTML",
            )
            return
        with open(path, "rb") as fh:
            data = fh.read()
        await channel.send_video(chat_id, data, caption="⬇️ via NAVIG · rapidok")
    except engine.TikTokUnavailable:
        await channel.send_message(chat_id, "Downloader unavailable — `pip install rapidok`.", parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok download failed: %s", exc)
        await channel.send_message(chat_id, "Couldn't download that video.", parse_mode=None)
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:  # noqa: BLE001
                pass


def _fallback_brief(meta: dict) -> str:
    bits = [meta.get("description") or ""]
    for c in (meta.get("comments") or [])[:5]:
        bits.append(f"• ({c['likes']}♥) {c['text'][:200]}")
    return "\n".join(b for b in bits if b)


def _brief_markdown(meta: dict, brief: str) -> str:
    """Markdown briefing for a rich message (heading + the AI's markdown body)."""
    head = f"🎵 **{meta.get('uploader') or 'TikTok'}**"
    head += f" · 🌍 {meta['country']}" if meta.get("country") else " · 🌍 country n/a"
    return f"{head}\n\n{brief}"
