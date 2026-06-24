"""Telegram Business layer: catch the owner's business-profile conversations and
alert on deletions.

SECURITY: a business conversation has two parties — the **owner** (you, the
business-account holder) and a **counterparty** (whoever messaged you). Every
message here is cataloged as **DATA ONLY**; none is ever routed to the command /
slash dispatch. Sender classification only decides whether an owner-only AI tool
may run (see :mod:`navig.telegram.permissions`). The counterparty can never reach
the system.
"""

from __future__ import annotations

import logging

from . import ai_actions, permissions

logger = logging.getLogger(__name__)

CFG_CONNECTIONS = "telegram.business.connections"   # {connection_id: {owner_id, can_reply}}
CFG_DELETION_ALERT = "telegram.business.deletion_alert"


def _cfg():
    from navig.core import Config
    return Config()


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


# ── Business connection registry (owner id ← connection id) ──────────────────


def remember_connection(connection_id: str, owner_id: int, *, can_reply: bool = False) -> None:
    cfg = _cfg()
    conns = dict(cfg.get(CFG_CONNECTIONS, {}) or {})
    conns[str(connection_id)] = {"owner_id": owner_id, "can_reply": bool(can_reply)}
    cfg.set(CFG_CONNECTIONS, conns, scope="global")
    cfg.save(scope="global")


def forget_connection(connection_id: str) -> None:
    cfg = _cfg()
    conns = dict(cfg.get(CFG_CONNECTIONS, {}) or {})
    conns.pop(str(connection_id), None)
    cfg.set(CFG_CONNECTIONS, conns, scope="global")
    cfg.save(scope="global")


def connection_owner(connection_id: str | None) -> int | None:
    if not connection_id:
        return None
    conns = _cfg().get(CFG_CONNECTIONS, {}) or {}
    rec = conns.get(str(connection_id))
    return rec.get("owner_id") if rec else None


def deletion_alert_enabled() -> bool:
    try:
        return bool(_cfg().get(CFG_DELETION_ALERT, True))
    except Exception:  # noqa: BLE001
        return True


def set_deletion_alert(value: bool) -> None:
    cfg = _cfg()
    cfg.set(CFG_DELETION_ALERT, bool(value), scope="global")
    cfg.save(scope="global")


# ── Update handlers (called from the bot channel's _process_update) ──────────


async def handle_business_connection(channel, conn: dict) -> None:
    """Bot connected to / disconnected from a business account. Record the owner id."""
    cid = conn.get("id")
    owner_id = conn.get("user_chat_id") or (conn.get("user") or {}).get("id")
    is_enabled = conn.get("is_enabled", True)
    can_reply = bool((conn.get("rights") or {}).get("can_reply", conn.get("can_reply", False)))
    if not cid:
        return
    if is_enabled and owner_id:
        remember_connection(cid, owner_id, can_reply=can_reply)
        logger.info("telegram business connection %s active (owner %s)", cid, owner_id)
    else:
        forget_connection(cid)
        logger.info("telegram business connection %s removed", cid)


async def handle_business_message(channel, msg: dict, *, edited: bool = False) -> None:
    """Catalog one business-conversation message (DATA only — never a command)."""
    if not permissions.business_enabled():
        return
    chat = msg.get("chat") or {}
    frm = msg.get("from") or {}
    chat_id = chat.get("id")
    message_id = msg.get("message_id")
    if chat_id is None or message_id is None:
        return
    sender_id = frm.get("id")
    owner_id = connection_owner(msg.get("business_connection_id"))
    is_owner = bool(owner_id and sender_id == owner_id)
    text = msg.get("text") or msg.get("caption") or ""
    try:
        _store().upsert_room(chat_id, type="business",
                             title=chat.get("title") or chat.get("first_name") or "")
        _store().upsert_message(
            chat_id, message_id,
            sender_id=sender_id,
            sender_name=(frm.get("username") or frm.get("first_name") or ""),
            date=str(msg.get("date") or ""), text=text, kind="business",
            edited_at=("yes" if edited else None),
            raw={"business": True, "from_owner": is_owner,
                 "connection_id": msg.get("business_connection_id")},
        )
    except Exception:  # noqa: BLE001
        logger.debug("business message catalog failed", exc_info=True)
    # IMPORTANT: business text is NEVER dispatched as a command. End of handling.


async def handle_deleted_business_messages(channel, payload: dict) -> None:
    """Owner-side deletion in a business conversation → DM the owner the cached
    content (only the owner; never the deck/other channels)."""
    if not (permissions.business_enabled() and deletion_alert_enabled()):
        return
    chat = payload.get("chat") or {}
    chat_id = chat.get("id")
    ids = payload.get("message_ids") or []
    chat_label = chat.get("title") or chat.get("username") or str(chat_id)
    for mid in ids:
        cached = None
        try:
            cached = _store().get_message_by_ref(chat_id, mid)
        except Exception:  # noqa: BLE001
            cached = None
        snippet = (cached or {}).get("text") if cached else None
        body = f"In {chat_label}:\n{snippet or '(content was not cached)'}"
        try:
            from navig.notify.router import NotificationRouter
            await NotificationRouter().dispatch(
                "telegram.message_deleted",
                "🗑 Message deleted",
                body,
                priority="high",
                only_channels=["telegram"],   # owner DM only — never deck/others
                data={"chat_id": chat_id, "message_id": mid},
            )
        except Exception:  # noqa: BLE001
            logger.debug("deletion alert dispatch failed", exc_info=True)
        try:
            _store().mark_message_deleted(chat_id, mid)
        except Exception:  # noqa: BLE001
            pass


async def run_reaction_action(channel, emoji: str, *, chat_id: int, message_id: int,
                              reactor_id: int, text: str) -> dict | None:
    """Owner reacted with an emoji on a (business or group) message → run the mapped
    sandboxed AI action, if permitted. Returns the action result or None (no mapping)."""
    tool = ai_actions.emoji_to_tool(emoji)
    if not tool:
        return None
    owner_id = None  # reactions: only the owner's own reactions trigger (caller passes reactor)
    # The caller already verified the reactor is the owner for owner-only tools; here we
    # re-check policy with is_owner based on allowed_users membership.
    is_owner = _is_owner_user(reactor_id)
    if tool in ai_actions.LLM_TOOLS:
        return await ai_actions.run_text_action(tool, text, is_owner=is_owner)
    return {"ok": False, "reason": "non_llm_tool_not_wired", "tool": tool}


def _is_owner_user(user_id: int | None) -> bool:
    if user_id is None:
        return False
    try:
        allowed = _cfg().get("telegram", {}).get("allowed_users") or []
        return int(user_id) in {int(x) for x in allowed}
    except Exception:  # noqa: BLE001
        return False
