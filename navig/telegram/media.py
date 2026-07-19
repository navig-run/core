"""Download a channel's media BYTES into a ChatExport-shaped staging folder.

The one primitive the MTProto engine was missing: :func:`history.sync_chat` catalogs
media *descriptors* (file_id/kind/size) but never fetches the actual file. This does —
reusing the same ``iter_messages`` loop and :func:`history._media_descriptor` classifier,
then calling Telethon's ``download_media``. Output is shaped exactly like a Telegram
Desktop export (``photos/`` ``video_files/`` ``files/`` + ``_catalog/manifest.jsonl``) so
the existing archive pipeline (driven by ``ARCHIVE_EXP``) ingests it with no special-casing.

Read-only on Telegram except for the download itself; message deletion is a separate,
confirm-gated step in the orchestrator. A message is recorded in ``tg_media_map.jsonl``
ONLY after its bytes land on disk — the invariant the safe-delete step relies on.
"""

from __future__ import annotations

import asyncio
import json
import os

from . import config
from .history import _media_descriptor
from .user_client import UserClient
from .util import extract_links

_SUBDIR = {"photo": "photos", "video": "video_files"}  # everything else → files/


def _subdir_for(kind: str) -> str:
    return _SUBDIR.get(kind, "files")


def _manifest_type(kind: str) -> str:
    if kind == "photo":
        return "image"
    if kind == "video":
        return "video"
    if kind in ("audio", "voice"):
        return "audio"
    return "file"


async def resolve_entity(client, chat):
    """Resolve a chat robustly. A raw numeric id often can't be resolved until the
    session entity cache is warm, and a bare positive channel/supergroup id needs the
    PeerChannel form. Try direct → warm dialogs → PeerChannel(-100)."""
    try:
        return await client.get_entity(chat)
    except (ValueError, TypeError):
        pass
    await client.get_dialogs()  # warm the entity cache (access hashes)
    try:
        return await client.get_entity(chat)
    except (ValueError, TypeError):
        try:
            n = int(chat)
        except (TypeError, ValueError):
            raise
        from telethon.tl.types import PeerChannel
        if n > 0:                       # bare channel/supergroup raw id → -100… peer
            return await client.get_entity(PeerChannel(n))
        raise


def _first_tiktok(text: str) -> str:
    for lk in extract_links(text or ""):
        if lk.get("provider") == "tiktok":
            return lk["url"]
    return ""


async def _resolve_source(client, ent, msg, cache) -> tuple[str, str]:
    """The TikTok source URL + caption for this media — from the message text, or from
    the message it replies to (the user's link that the bot answered). Cached per parent."""
    url = _first_tiktok(msg.message)
    if url:
        return url, (msg.message or "")
    rid = getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None)
    if not rid:
        return "", (msg.message or "")
    if rid in cache:
        return cache[rid]
    ptext = ""
    try:
        parent = await client.get_messages(ent, ids=rid)
        ptext = getattr(parent, "message", "") or ""
    except Exception:  # noqa: BLE001 — parent unavailable → no source url, not fatal
        ptext = ""
    res = (_first_tiktok(ptext), ptext or (msg.message or ""))
    cache[rid] = res
    return res


async def scan_channel(chat, *, limit=300, from_sender=None) -> dict:
    """Light read-only scan for preview / auto-detect: how many media messages and how
    many TikTok links appear in the most recent ``limit`` messages. No downloads."""
    media = links = 0
    title = str(chat)
    async with UserClient() as c:
        ent = await resolve_entity(c, chat)
        title = getattr(ent, "title", None) or getattr(ent, "first_name", "") or str(chat)
        sender_id = None
        if from_sender not in (None, ""):
            try:
                sender_id = (await c.get_entity(from_sender)).id
            except Exception:  # noqa: BLE001
                sender_id = None
        async for msg in c.iter_messages(ent, limit=limit):
            if _media_descriptor(msg) and (sender_id is None or getattr(msg, "sender_id", None) == sender_id):
                media += 1
            if _first_tiktok(msg.message):
                links += 1
    return {"media": media, "links": links, "title": title}


async def download_channel_media(chat, *, dest, from_sender=None, ids=None,
                                 limit=None, since_id=None, progress=None) -> dict:
    """Download every media message from ``chat`` into a ChatExport-shaped folder.

    Writes ``photos/`` ``video_files/`` ``files/`` plus ``_catalog/manifest.jsonl``
    (01-schema, so the pipeline's HTML parser is skipped) and ``_catalog/tg_media_map.jsonl``
    (message_id ↔ staged rel, written only on a successful download). ``from_sender``
    restricts to one sender (the bot); ``since_id`` skips messages ≤ that id (re-run guard).

    Returns ``{dest, media, by_kind, message_ids, skipped, title}``.
    """
    dest = os.path.abspath(str(dest))
    cat = os.path.join(dest, "_catalog")
    for d in ("photos", "video_files", "files", "_catalog"):
        os.makedirs(os.path.join(dest, d), exist_ok=True)

    every, secs = config.throttle()
    id_list = [int(x) for x in ids] if ids else None

    by_kind: dict[str, int] = {}
    message_ids: list[int] = []
    skipped: list[dict] = []
    src_cache: dict[int, tuple[str, str]] = {}
    title = str(chat)
    n = 0

    man_p = os.path.join(cat, "manifest.jsonl")
    map_p = os.path.join(cat, "tg_media_map.jsonl")
    async with UserClient() as c:
        ent = await resolve_entity(c, chat)
        title = getattr(ent, "title", None) or getattr(ent, "first_name", "") or str(chat)
        sender_id = None
        if from_sender not in (None, ""):
            try:
                sender_id = (await c.get_entity(from_sender)).id
            except Exception:  # noqa: BLE001 — unknown sender → import everything
                sender_id = None

        with open(man_p, "a", encoding="utf-8") as man, open(map_p, "a", encoding="utf-8") as mapf:
            async for msg in c.iter_messages(ent, limit=limit, ids=id_list, min_id=(since_id or 0)):
                if msg is None:
                    continue
                desc = _media_descriptor(msg)
                if not desc:
                    continue
                if sender_id is not None and getattr(msg, "sender_id", None) != sender_id:
                    continue
                sub = _subdir_for(desc["kind"])
                out_base = os.path.join(dest, sub, str(msg.id))
                try:
                    path = await c.download_media(msg, file=out_base)
                except Exception as exc:  # noqa: BLE001 — one bad file must not abort the run
                    skipped.append({"message_id": msg.id, "reason": str(exc)[:120]})
                    continue
                if not path or not os.path.exists(path):
                    skipped.append({"message_id": msg.id, "reason": "download returned no file"})
                    continue
                rel = f"{sub}/{os.path.basename(path)}"
                source_url, caption = await _resolve_source(c, ent, msg, src_cache)
                man.write(json.dumps({
                    "msg_id": msg.id,
                    "date": str(getattr(msg, "date", "") or ""),
                    "type": _manifest_type(desc["kind"]),
                    "path": rel,
                    "caption": caption,
                    "links": [lk["url"] for lk in extract_links(caption)],
                }, ensure_ascii=False) + "\n")
                mapf.write(json.dumps({
                    "message_id": msg.id,
                    "grouped_id": getattr(msg, "grouped_id", None),
                    "rel": rel,
                    "size": desc.get("size"),
                    "kind": desc["kind"],
                    "source_url": source_url,
                }, ensure_ascii=False) + "\n")
                man.flush()
                mapf.flush()          # durable per-row: an interrupted import keeps its mapping
                by_kind[desc["kind"]] = by_kind.get(desc["kind"], 0) + 1
                message_ids.append(msg.id)
                if progress:
                    progress(len(message_ids), desc["kind"])
                n += 1
                if every and n % every == 0:
                    await asyncio.sleep(secs)

    try:  # friendly channel name for the pipeline's sources.json
        json.dump({"name": title}, open(os.path.join(dest, "result.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass

    return {"dest": dest, "media": len(message_ids), "by_kind": by_kind,
            "message_ids": message_ids, "skipped": skipped, "title": title}
