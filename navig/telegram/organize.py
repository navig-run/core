"""Organize operations over MTProto: forward, move (copy+delete), rename, delete,
link extraction. Destructive ops are confirm-gated and default to a dry-run so the
owner always previews before anything is changed. The owner drives these from the
CLI/deck only.
"""

from __future__ import annotations

import logging

from .user_client import UserClient
from .util import extract_links

logger = logging.getLogger(__name__)


def _ids(message_ids) -> list[int]:
    if isinstance(message_ids, (int, str)):
        message_ids = [message_ids]
    return [int(x) for x in message_ids]


async def forward(from_chat, message_ids, to_chat, *, drop_author: bool = False) -> dict:
    """Forward (or copy, with ``drop_author``) messages to another chat/channel."""
    ids = _ids(message_ids)
    async with UserClient() as c:
        frm = await c.get_entity(from_chat)
        to = await c.get_entity(to_chat)
        try:
            sent = await c.forward_messages(to, ids, frm, drop_author=drop_author)
        except TypeError:  # older telethon without drop_author
            sent = await c.forward_messages(to, ids, frm)
        n = len(sent) if isinstance(sent, list) else (1 if sent else 0)
        return {"forwarded": n, "from": frm.id, "to": to.id, "drop_author": drop_author}


async def move(from_chat, message_ids, to_chat, *, drop_author: bool = True,
               confirm: bool = False) -> dict:
    """Move = forward to ``to_chat`` then delete the originals. **Destructive** — does
    nothing unless ``confirm=True`` (returns a dry-run preview otherwise)."""
    ids = _ids(message_ids)
    if not confirm:
        return {"dry_run": True, "would_move": len(ids), "from": str(from_chat),
                "to": str(to_chat), "note": "pass confirm=True to actually move (copy then delete)"}
    fwd = await forward(from_chat, ids, to_chat, drop_author=drop_author)
    async with UserClient() as c:
        frm = await c.get_entity(from_chat)
        await c.delete_messages(frm, ids, revoke=True)
    return {"moved": fwd["forwarded"], "deleted": len(ids), "from": fwd["from"], "to": fwd["to"]}


async def delete_messages(chat, message_ids, *, confirm: bool = False) -> dict:
    """Delete messages (revoke for all). Confirm-gated; dry-run otherwise."""
    ids = _ids(message_ids)
    if not confirm:
        return {"dry_run": True, "would_delete": len(ids), "chat": str(chat)}
    async with UserClient() as c:
        ent = await c.get_entity(chat)
        await c.delete_messages(ent, ids, revoke=True)
    return {"deleted": len(ids), "chat": str(chat)}


async def rename(chat, title: str, *, confirm: bool = False) -> dict:
    """Rename a chat/channel title (requires admin rights). Confirm-gated."""
    if not confirm:
        return {"dry_run": True, "chat": str(chat), "new_title": title,
                "note": "pass confirm=True to apply"}
    async with UserClient() as c:
        ent = await c.get_entity(chat)
        from telethon.tl.functions.channels import EditTitleRequest
        from telethon.tl.functions.messages import EditChatTitleRequest
        try:
            await c(EditTitleRequest(channel=ent, title=title))
        except Exception:  # noqa: BLE001 — basic group, not a channel
            await c(EditChatTitleRequest(chat_id=ent.id, title=title))
    return {"renamed": True, "chat": ent.id, "new_title": title}


async def rename_many(specs: list[dict], *, confirm: bool = False,
                      delay: float = 2.0, max_wait: float = 1200.0,
                      max_flood_retries: int = 8, progress=None) -> list[dict]:
    """Rename many chats in one client session, flood-safe for unattended runs.

    ``specs`` = ``[{"chat": id|@user, "title": str}]``. Reuses one connection, sleeps
    ``delay`` s between edits, and on ``FloodWaitError`` waits the requested time and
    retries — up to ``max_flood_retries`` times per chat, skipping any single wait
    longer than ``max_wait`` s. ``progress(dict)`` is called after each chat (for live
    logging). Dry-run unless ``confirm``. Returns ``{chat, title, status}`` per spec,
    status ∈ dry_run|renamed|error.
    """
    import asyncio

    from telethon.errors import FloodWaitError
    from telethon.tl.functions.channels import EditTitleRequest
    from telethon.tl.functions.messages import EditChatTitleRequest
    from telethon.tl.types import Chat as _TLChat

    if not confirm:
        return [{"chat": s["chat"], "title": s["title"], "status": "dry_run"} for s in specs]

    async def _edit(client, ent, title):
        # Branch by real type so the true error surfaces (don't mask admin errors
        # by blindly retrying the basic-group API on a channel/megagroup).
        if isinstance(ent, _TLChat):  # legacy basic group
            await client(EditChatTitleRequest(chat_id=ent.id, title=title))
        else:  # Channel — broadcast or megagroup
            await client(EditTitleRequest(channel=ent, title=title))

    def _emit(ev: dict) -> None:
        # A logging/encoding error in the caller must never abort the run.
        if progress:
            try:
                progress(ev)
            except Exception:  # noqa: BLE001
                pass

    out: list[dict] = []
    async with UserClient() as c:
        await c.get_dialogs()  # warm the entity cache so id refs resolve
        for i, s in enumerate(specs):
            chat, title = s["chat"], s["title"]
            result = {"chat": chat, "title": title, "status": "error", "error": "unknown"}
            for _ in range(max_flood_retries + 1):
                try:
                    ent = await c.get_entity(chat)
                    await _edit(c, ent, title)
                    result = {"chat": chat, "title": title, "status": "renamed"}
                    break
                except FloodWaitError as fw:
                    if fw.seconds > max_wait:
                        result = {"chat": chat, "title": title, "status": "error",
                                  "error": f"flood {fw.seconds}s > max_wait"}
                        break
                    logger.warning("flood wait %ss on rename %s", fw.seconds, chat)
                    _emit({"chat": chat, "status": "flood_wait", "seconds": fw.seconds})
                    await asyncio.sleep(fw.seconds + 1)
                except Exception as exc:  # noqa: BLE001 — e.g. not admin / no change_info
                    result = {"chat": chat, "title": title, "status": "error", "error": str(exc)}
                    break
            out.append(result)
            _emit(result)
            if delay and i < len(specs) - 1:
                await asyncio.sleep(delay)
    return out


async def links(chat, *, limit: int | None = 500) -> dict:
    """Scan a chat's recent messages and return a deduped link index
    (tiktok / youtube / telegram / url)."""
    found: list[dict] = []
    seen: set[str] = set()
    async with UserClient() as c:
        ent = await c.get_entity(chat)
        async for msg in c.iter_messages(ent, limit=limit):
            for link in extract_links(msg.message or ""):
                if link["url"] in seen:
                    continue
                seen.add(link["url"])
                found.append({**link, "message_id": msg.id, "date": str(getattr(msg, "date", "") or "")})
    by_provider: dict[str, int] = {}
    for link in found:
        by_provider[link["provider"]] = by_provider.get(link["provider"], 0) + 1
    return {"chat": str(chat), "total": len(found), "by_provider": by_provider, "links": found}
