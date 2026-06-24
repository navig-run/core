"""List/resolve dialogs (groups, channels, DMs) and forum topics via MTProto."""

from __future__ import annotations

import logging

from .user_client import UserClient

logger = logging.getLogger(__name__)


def _kind(entity) -> str:
    """channel | supergroup | group | user | unknown."""
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    if entity.__class__.__name__ == "Chat":
        return "group"
    if entity.__class__.__name__ == "User":
        return "user"
    return "unknown"


def _username_url(entity) -> str | None:
    u = getattr(entity, "username", None)
    return f"https://t.me/{u}" if u else None


async def list_dialogs(*, kinds: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """Return every dialog with metadata. ``kinds`` filters (channel/supergroup/group/user)."""
    out: list[dict] = []
    async with UserClient() as c:
        async for d in c.iter_dialogs(limit=limit):
            ent = d.entity
            k = _kind(ent)
            if kinds and k not in kinds:
                continue
            out.append({
                "chat_id": d.id,
                "kind": k,
                "title": d.name or "",
                "username": getattr(ent, "username", None),
                "url": _username_url(ent),
                "is_forum": bool(getattr(ent, "forum", False)),
                "unread": d.unread_count,
                "members": getattr(ent, "participants_count", None),
                "archived": getattr(d, "archived", False),
            })
    return out


async def list_topics(chat: str | int) -> list[dict]:
    """Return forum topics for a forum supergroup → ``[{topic_id, title, icon}]``."""
    from telethon.tl.functions.channels import GetForumTopicsRequest

    out: list[dict] = []
    async with UserClient() as c:
        entity = await c.get_entity(chat)
        res = await c(GetForumTopicsRequest(
            channel=entity, offset_date=0, offset_id=0, offset_topic=0, limit=100,
        ))
        for t in getattr(res, "topics", []):
            out.append({
                "topic_id": getattr(t, "id", None),
                "title": getattr(t, "title", ""),
                "icon_color": getattr(t, "icon_color", None),
                "closed": getattr(t, "closed", False),
                "pinned": getattr(t, "pinned", False),
            })
    return out


async def resolve(chat: str | int) -> dict:
    """Resolve a chat ref (id / @username / t.me link) to ``{chat_id, kind, title}``."""
    async with UserClient() as c:
        ent = await c.get_entity(chat)
        return {
            "chat_id": ent.id,
            "kind": _kind(ent),
            "title": getattr(ent, "title", None) or getattr(ent, "first_name", "") or "",
            "username": getattr(ent, "username", None),
            "is_forum": bool(getattr(ent, "forum", False)),
        }
