"""Full-history backfill: scan a chat over MTProto and upsert into the existing
``TelegramCatalogStore`` (tg_rooms / tg_messages / tg_media), so deck/CLI search
covers EVERYTHING — closing the "no backfill without MTProto" gap. Read-only on
Telegram's side; flood-safe throttled.
"""

from __future__ import annotations

import asyncio
import logging

from . import config
from .user_client import UserClient
from .util import extract_links

logger = logging.getLogger(__name__)

_store_cache = None


def _store():
    global _store_cache
    if _store_cache is None:
        from navig.store.telegram_catalog import TelegramCatalogStore
        _store_cache = TelegramCatalogStore()
    return _store_cache


def _media_descriptor(msg) -> dict | None:
    """Extract a media row from a Telethon message, or None."""
    doc = getattr(msg, "document", None)
    photo = getattr(msg, "photo", None)
    if photo is not None:
        return {"kind": "photo", "file_unique_id": str(getattr(photo, "id", "")) or None,
                "file_id": str(getattr(photo, "id", "")) or None, "mime": "image/jpeg",
                "size": None, "filename": None}
    if doc is not None:
        from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeFilename, DocumentAttributeVideo
        mime = str(getattr(doc, "mime_type", "") or "")
        kind = "document"
        filename = None
        for attr in getattr(doc, "attributes", []):
            if isinstance(attr, DocumentAttributeAudio):
                kind = "voice" if getattr(attr, "voice", False) else "audio"
            elif isinstance(attr, DocumentAttributeVideo):
                kind = "video"
            elif isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
        if mime.startswith("image"):
            kind = "photo"
        return {"kind": kind, "file_unique_id": str(getattr(doc, "id", "")) or None,
                "file_id": str(getattr(doc, "id", "")) or None, "mime": mime or None,
                "size": getattr(doc, "size", None), "filename": filename}
    return None


async def sync_chat(chat: str | int, *, limit: int | None = None, progress=None) -> dict:
    """Backfill one chat into the catalog. ``progress(n, kind)`` is called per message.

    Returns ``{chat_id, title, messages, media, links}``.
    """
    every, secs = config.throttle()
    msgs = media_n = links_n = 0
    chat_id = None
    title = ""
    async with UserClient() as c:
        ent = await c.get_entity(chat)
        chat_id = ent.id
        title = getattr(ent, "title", None) or getattr(ent, "first_name", "") or ""
        _store().upsert_room(
            chat_id,
            type=("channel" if getattr(ent, "broadcast", False)
                  else "supergroup" if getattr(ent, "megagroup", False)
                  else "user" if ent.__class__.__name__ == "User" else "group"),
            title=title, username=getattr(ent, "username", None),
            member_count=getattr(ent, "participants_count", None), touch_sync=True,
        )
        n = 0
        async for msg in c.iter_messages(ent, limit=limit):
            text = msg.message or ""
            sender_id = getattr(msg, "sender_id", None)
            topic_id = getattr(getattr(msg, "reply_to", None), "reply_to_top_id", None)
            media = _media_descriptor(msg)
            media_ref = None
            if media:
                media_ref = _store().upsert_media(
                    chat_id, message_id=msg.id, file_id=media["file_id"],
                    file_unique_id=media["file_unique_id"], kind=media["kind"],
                    mime=media["mime"], size=media["size"], filename=media["filename"],
                )
                media_n += 1
            _store().upsert_message(
                chat_id, msg.id,
                sender_id=sender_id, date=str(getattr(msg, "date", "") or ""),
                text=text, reply_to=getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None),
                media_ref=media_ref, kind=(media["kind"] if media else "text"),
                raw={"topic_id": topic_id, "out": bool(getattr(msg, "out", False))},
            )
            msgs += 1
            for _link in extract_links(text):
                try:
                    _store().add_link(chat_id, _link["url"], _link["provider"], message_id=msg.id)
                except Exception:  # noqa: BLE001
                    pass
                links_n += 1
            if progress:
                progress(msgs, media["kind"] if media else "text")
            n += 1
            if every and n % every == 0:
                await asyncio.sleep(secs)
    return {"chat_id": chat_id, "title": title, "messages": msgs, "media": media_n, "links": links_n}


def _audio_attrs(msg) -> dict | None:
    """performer/title/file_name/duration from a Telethon audio message, else None."""
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeFilename

    doc = getattr(msg, "document", None)
    if not doc:
        return None
    performer = title = file_name = ""
    duration = None
    for attr in getattr(doc, "attributes", []):
        if isinstance(attr, DocumentAttributeAudio):
            performer, title, duration = attr.performer or "", attr.title or "", attr.duration
        elif isinstance(attr, DocumentAttributeFilename):
            file_name = attr.file_name or ""
    is_audio = duration is not None or str(getattr(doc, "mime_type", "")).startswith("audio")
    if not is_audio:
        return None
    return {"performer": performer, "title": title, "file_name": file_name, "duration": duration,
            "file_size": getattr(doc, "size", None), "file_unique_id": str(getattr(doc, "id", "")) or None}


async def collect_dedupe_records(chat: str | int, *, limit: int | None = None) -> list[dict]:
    """Live-scan a chat for audio messages with the attrs dedupe needs
    (performer/title/duration/file_unique_id/topic/message_id). Read-only + throttled."""
    every, secs = config.throttle()
    records: list[dict] = []
    async with UserClient() as c:
        ent = await c.get_entity(chat)
        n = 0
        async for msg in c.iter_messages(ent, limit=limit):
            a = _audio_attrs(msg)
            if not a:
                continue
            a.update({
                "chat_id": ent.id, "message_id": msg.id,
                "topic_id": getattr(getattr(msg, "reply_to", None), "reply_to_top_id", None),
                "topic": "",  # resolved by topic title later if available
            })
            records.append(a)
            n += 1
            if every and n % every == 0:
                await asyncio.sleep(secs)
    return records


async def sync_all(*, kinds: list[str] | None = None, limit_per_chat: int | None = None,
                   progress=None) -> dict:
    """Backfill every (or selected-kind) dialog. Returns aggregate counts + per-chat."""
    from .dialogs import list_dialogs

    dials = await list_dialogs(kinds=kinds)
    results = []
    total_msgs = total_media = 0
    for d in dials:
        try:
            r = await sync_chat(d["chat_id"], limit=limit_per_chat, progress=progress)
            results.append(r)
            total_msgs += r["messages"]
            total_media += r["media"]
        except Exception as exc:  # noqa: BLE001 — one chat failing shouldn't abort the run
            logger.warning("history sync failed for %s: %s", d.get("title"), exc)
            results.append({"chat_id": d["chat_id"], "title": d.get("title"), "error": str(exc)})
    return {"chats": len(dials), "messages": total_msgs, "media": total_media, "per_chat": results}
