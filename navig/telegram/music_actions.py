"""Bot-side music-link action: detect a music-service link in a 1:1 chat and
reply with the SAME track's links across every platform (song.link / Odesli).

Wires :func:`navig_download.music_links.resolve_links` (the optional
**navig-download** plugin) into the Telegram bot. When that plugin isn't
installed this module fails to import; the single call-site in
``gateway/channels/telegram.py`` guards the import in ``try/except`` and skips,
so core stays inert (same contract as :mod:`navig.telegram.tiktok_actions`).

Deliberately conservative UX (the Operator way — no surprise spam):
  * **DM-only** — the caller never invokes this in groups.
  * **bare-link only** — fires only when the message is essentially just the
    link, so a conversational message that merely mentions a link is left to the
    agent (no double reply).
  * **opt-out** via ``telegram.music_links.enabled`` (default on).
Resolution is a public, keyless song.link lookup — no auth, no cost, no PII — so
it needs no per-tool permission gate beyond the inbound auth already applied.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging

from navig_download.music_links import MusicResolveError, find_music_url, resolve_links

logger = logging.getLogger(__name__)

_CFG_ENABLED = "telegram.music_links.enabled"


def _coerce_bool(value: object, default: bool) -> bool:
    """Tolerate ``navig config set`` writing bools as raw strings ('false')."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in ("false", "0", "no", "off", "")


def enabled() -> bool:
    """Whether music-link auto-reply is on (default True; opt-out via config)."""
    try:
        from navig.core import Config

        return _coerce_bool(Config().get(_CFG_ENABLED, True), True)
    except Exception:  # noqa: BLE001 — config unreadable → keep the default on
        return True


def _is_bare_link(text: str, url: str) -> bool:
    """True when *text* is essentially just *url* (a share), not a sentence that
    happens to contain a link — those belong to the chat agent, not to us."""
    remainder = (text or "").replace(url, "", 1).strip(" \t\r\n-–—·:|.,!?\"'()[]")
    return not remainder


def _format(data: dict) -> str:
    title, artist = data.get("title"), data.get("artist")
    if title and artist:
        head = f"🎵 <b>{_html.escape(title)}</b> · <i>{_html.escape(artist)}</i>"
    elif title:
        head = f"🎵 <b>{_html.escape(title)}</b>"
    else:
        head = "🎵 <b>Track links</b>"
    lines = [head, ""]
    for link in data.get("links", []):
        url = link.get("url")
        if url:
            lines.append(f'<a href="{_html.escape(url)}">{_html.escape(link.get("label") or url)}</a>')
    return "\n".join(lines)


async def _resolve_and_send(channel, chat_id: int, url: str, reply_to_message_id: int | None) -> bool:
    """Resolve *url* via song.link and reply with the platform links. Returns True
    if a reply was sent; False on any resolve/send failure (caller decides UX)."""
    try:
        data = await asyncio.to_thread(resolve_links, url)
    except MusicResolveError:
        return False  # not on song.link
    except Exception as exc:  # noqa: BLE001
        logger.debug("music resolve failed: %s", exc)
        return False
    try:
        await channel.send_message(
            chat_id, _format(data), parse_mode="HTML", reply_to_message_id=reply_to_message_id,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("music send failed: %s", exc)
        return False


async def offer_links(channel, chat_id: int, message_id: int, text: str) -> bool:
    """PASSIVE: if *text* is a bare music-service link, reply with the same track
    across every platform (song.link). Returns True if a reply was sent (so the
    caller can OWN the message); a silent no-op otherwise — disabled, no link, not
    a bare link, or unresolvable.
    """
    if not text or not enabled():
        return False
    url = find_music_url(text)
    if not url or not _is_bare_link(text, url):
        return False
    return await _resolve_and_send(channel, chat_id, url, message_id)


async def resolve_reply(channel, chat_id: int, reply_to_message_id: int, url: str) -> bool:
    """EXPLICIT ('music' reply-keyword): resolve *url* and reply with its links.
    No enabled()/bare-link gate — the user named it, so it runs regardless of the
    passive toggle and works in groups. Returns True if links were sent."""
    return await _resolve_and_send(channel, chat_id, url, reply_to_message_id)
