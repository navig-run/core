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


def _rename_rights(entity) -> tuple[bool, bool, bool]:
    """(creator, admin, can_rename) for a chat entity.

    ``can_rename`` is True when you can edit the title: you're the creator, or an
    admin whose rights include ``change_info``. Users/DMs are never renamable.
    """
    creator = bool(getattr(entity, "creator", False))
    rights = getattr(entity, "admin_rights", None)
    admin = creator or rights is not None
    can_rename = creator or bool(getattr(rights, "change_info", False))
    # Basic (legacy) groups: any participant can edit info unless restricted.
    if entity.__class__.__name__ == "Chat" and not getattr(entity, "admins_enabled", False):
        can_rename = True
    return creator, admin, can_rename


async def list_dialogs(*, kinds: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """Return every dialog with metadata. ``kinds`` filters (channel/supergroup/group/user)."""
    out: list[dict] = []
    async with UserClient() as c:
        async for d in c.iter_dialogs(limit=limit):
            ent = d.entity
            k = _kind(ent)
            if kinds and k not in kinds:
                continue
            creator, admin, can_rename = _rename_rights(ent)
            out.append({
                "chat_id": d.id,
                "raw_id": int(getattr(ent, "id", 0)),
                "kind": k,
                "title": d.name or "",
                "username": getattr(ent, "username", None),
                "url": _username_url(ent),
                "is_forum": bool(getattr(ent, "forum", False)),
                "unread": d.unread_count,
                "members": getattr(ent, "participants_count", None),
                "archived": getattr(d, "archived", False),
                "creator": creator,
                "admin": admin,
                "can_rename": can_rename,
            })
    return out


def _get_forum_topics_request():
    """``GetForumTopicsRequest``, wherever this telethon keeps it.

    Telegram moved the forum-topic methods from the ``channels`` namespace to
    ``messages``; telethon followed in 1.44, and the old import raises ImportError at
    call time — so ``navig telegram topics`` was dead on any current install. Try the
    new home first, fall back to the old one so older telethons keep working.
    """
    try:
        from telethon.tl.functions.messages import GetForumTopicsRequest
    except ImportError:  # telethon < 1.44 — still under channels
        from telethon.tl.functions.channels import GetForumTopicsRequest
    return GetForumTopicsRequest


async def list_topics(chat: str | int, *, limit: int | None = None,
                      page: int = 100) -> list[dict]:
    """Return forum topics for a forum supergroup → ``[{topic_id, title, icon}]``.

    Pages through the whole forum. The previous single request with ``limit=100``
    silently truncated any forum with more topics than that — the failure mode where
    you archive a group and quietly miss a third of it.
    """
    from .media import resolve_entity  # coerces a numeric chat id passed as a string

    req = _get_forum_topics_request()
    out: list[dict] = []
    seen: set = set()
    async with UserClient() as c:
        entity = await resolve_entity(c, chat)
        offset_date, offset_id, offset_topic = None, 0, 0
        while True:
            take = page if limit is None else max(1, min(page, limit - len(out)))
            res = await c(req(
                peer=entity, offset_date=offset_date, offset_id=offset_id,
                offset_topic=offset_topic, limit=take,
            ))
            topics = list(getattr(res, "topics", []) or [])
            if not topics:
                break
            # top_message dates live in the response's `messages`, not on the topic
            dates = {getattr(m, "id", None): getattr(m, "date", None)
                     for m in getattr(res, "messages", []) or []}
            for t in topics:
                # pages can overlap; a topic must appear once however often it is served
                tid = getattr(t, "id", None)
                if tid in seen:
                    continue
                seen.add(tid)
                out.append({
                    "topic_id": getattr(t, "id", None),
                    "title": getattr(t, "title", ""),
                    "icon_color": getattr(t, "icon_color", None),
                    "closed": bool(getattr(t, "closed", False)),
                    "pinned": bool(getattr(t, "pinned", False)),
                    "top_message": getattr(t, "top_message", None),
                })
            if limit is not None and len(out) >= limit:
                return out[:limit]
            last = topics[-1]
            nxt_topic = getattr(last, "id", None)
            if nxt_topic in (None, offset_topic):
                break                      # no forward progress — stop rather than spin
            offset_topic = nxt_topic
            offset_id = getattr(last, "top_message", 0) or 0
            offset_date = dates.get(offset_id)
            if len(topics) < take:
                break                      # last page
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
