"""Unified search across the Telegram catalog (FTS over all backfilled
conversations + media) plus optional live MTProto search.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def search(q: str, *, chat_id: int | None = None, limit: int = 50) -> list[dict]:
    """Full-text search the catalog (everything backfilled via history sync).
    Synchronous; needs no Telegram connection."""
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore().search(q, chat_id=chat_id, limit=limit)


async def search_live(chat, q: str, *, limit: int = 50) -> list[dict]:
    """Live MTProto search inside one chat (covers messages not yet backfilled)."""
    from .user_client import UserClient

    out: list[dict] = []
    async with UserClient() as c:
        ent = await c.get_entity(chat)
        async for msg in c.iter_messages(ent, search=q, limit=limit):
            out.append({
                "chat_id": ent.id,
                "message_id": msg.id,
                "date": str(getattr(msg, "date", "") or ""),
                "sender_id": getattr(msg, "sender_id", None),
                "text": msg.message or "",
            })
    return out
