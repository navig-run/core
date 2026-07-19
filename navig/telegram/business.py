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
import random

from . import ai_actions, autoreply, biz_commands, permissions, reply_actions

logger = logging.getLogger(__name__)

CFG_CONNECTIONS = "telegram.business.connections"   # {connection_id: {owner_id, can_reply}}
CFG_DELETION_ALERT = "telegram.business.deletion_alert"
CFG_PING = "telegram.business.ping.who"             # owner | both | off (default owner)

import re as _re  # noqa: E402

_PING_RE = _re.compile(r"^\s*/?ping(@\w+)?\s*$", _re.IGNORECASE)

# Short, playful pong variants (ping in a business chat replies just one of these).
_PONGS = (
    "🏓 pong", "🏓 king pong", "🏓 pong gong", "🏓 gnop", "🏓 ponguuuuuuuuuuuuuuuuuuuuuuw",
    "🏓 pong pong", "🏓 p0ng", "🏓 pongo", "🏓 pongggg", "🏓 ping? pong.", "🏓 pôńg",
)


def _cfg():
    from navig.core import Config
    return Config()


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


def _bot_id(channel) -> str:
    """The bot's own numeric user id (the prefix of its token) — used to detect
    the bot's own messages echoed back by Telegram in business conversations."""
    try:
        tok = getattr(channel, "bot_token", "") or ""
        return tok.split(":", 1)[0] if ":" in tok else ""
    except Exception:  # noqa: BLE001
        return ""


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


def _owner_from_allowed() -> int | None:
    """The configured owner (the single allowed Telegram user). Fallback for when
    the one-time ``business_connection`` update was never captured (e.g. the cloud
    uplink was offline when the bot was connected)."""
    try:
        tg = _cfg().get("telegram", {}) or {}
        allowed = tg.get("allowed_users") or []
        ints = [int(x) for x in allowed if str(x).lstrip("-").isdigit()]
        return ints[0] if ints else None
    except Exception:  # noqa: BLE001
        return None


def resolve_owner(connection_id: str | None) -> int | None:
    """Owner id for a business connection — registry first, else the configured
    owner. On fallback we cache the connection so future lookups + reply targeting
    work without re-receiving the (one-time) connection update."""
    oid = connection_owner(connection_id)
    if oid is not None:
        return oid
    oid = _owner_from_allowed()
    if oid is not None and connection_id:
        try:
            remember_connection(connection_id, oid, can_reply=True)
            logger.info("business: auto-registered connection %s → owner %s (fallback)",
                        connection_id, oid)
        except Exception:  # noqa: BLE001
            pass
    return oid


def deletion_alert_enabled() -> bool:
    try:
        return bool(_cfg().get(CFG_DELETION_ALERT, True))
    except Exception:  # noqa: BLE001
        return True


def set_deletion_alert(value: bool) -> None:
    cfg = _cfg()
    cfg.set(CFG_DELETION_ALERT, bool(value), scope="global")
    cfg.save(scope="global")


# ── Ping (the one safe canned reply in business chats) ───────────────────────


def ping_policy() -> str:
    """Who may get a /ping reply in a business chat: owner | both | off."""
    try:
        v = str(_cfg().get(CFG_PING, "owner") or "owner").lower()
        return v if v in ("owner", "both", "off") else "owner"
    except Exception:  # noqa: BLE001
        return "owner"


def set_ping_policy(who: str) -> None:
    if who not in ("owner", "both", "off"):
        raise ValueError("who must be one of owner|both|off")
    cfg = _cfg()
    cfg.set(CFG_PING, who, scope="global")
    cfg.save(scope="global")


def _catalog_stats() -> dict[str, int]:
    out = {"messages": 0, "rooms": 0, "media": 0}
    try:
        store = _store()
        for key, sql in (
            ("messages", "SELECT COUNT(*) AS c FROM tg_messages WHERE deleted = 0"),
            ("rooms", "SELECT COUNT(*) AS c FROM tg_rooms"),
            ("media", "SELECT COUNT(*) AS c FROM tg_media"),
        ):
            row = store._read_one(sql)
            if row is not None:
                out[key] = int(row["c"] or 0)
    except Exception:  # noqa: BLE001
        pass
    return out


async def _send_business_reply(channel, chat_id, text, business_connection_id=None,
                               parse_mode: str = "HTML") -> None:
    """Reply INTO a business conversation. The bot posts as the business account, so
    it needs ``business_connection_id`` (plain send_message can't do that). Falls
    back to a plain send if the connection id is missing."""
    data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if business_connection_id:
        data["business_connection_id"] = business_connection_id
    try:
        await channel._api_call("sendMessage", data)
    except Exception:  # noqa: BLE001
        try:
            await channel.send_message(chat_id, text, parse_mode=parse_mode)
        except Exception:  # noqa: BLE001
            logger.debug("business ping reply failed", exc_info=True)


async def handle_ping(channel, msg: dict, *, is_owner: bool) -> bool:
    """Reply to ``/ping`` (or bare ``ping``) in a business chat with a live status.

    This is the ONE controlled exception to "business text is never a command": a
    fixed, no-argument, no-system-access health check — a canned reply plus
    read-only catalog counts. It NEVER reaches the CLI/system/skills/dispatch.
    Owner-gated by default (``telegram.business.ping.who``: owner|both|off)."""
    text = msg.get("text") or msg.get("caption") or ""
    if not _PING_RE.match(text):
        return False
    who = ping_policy()
    if who == "off" or (not is_owner and who != "both"):
        return False
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return False
    # Short, playful pong (the full status report lives in the bot's own DM).
    body = random.choice(_PONGS)
    await _send_business_reply(channel, chat_id, body, msg.get("business_connection_id"),
                               parse_mode=None)
    return True


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
    # ── Loop guard ──────────────────────────────────────────────────────────
    # Telegram echoes the bot's OWN business sends back as business_message
    # updates (from = the bot). Without this, pro-mode auto-reply would answer its
    # own replies forever. Skip anything the bot itself sent.
    if frm.get("is_bot") or (sender_id is not None and str(sender_id) == _bot_id(channel)):
        return
    owner_id = resolve_owner(msg.get("business_connection_id"))
    is_owner = bool(owner_id and sender_id == owner_id)
    text = msg.get("text") or msg.get("caption") or ""
    logger.info(
        "business message: chat=%s from=%s owner=%s is_owner=%s text=%.50r",
        chat_id, sender_id, owner_id, is_owner, text,
    )
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
    # Owner pro-mode control ("role … on/off") — owner-only; deletes the command
    # and toggles AI persona auto-reply for this conversation.
    try:
        if await autoreply.handle_command(channel, msg, is_owner=is_owner, owner_id=owner_id):
            return
    except Exception:  # noqa: BLE001
        logger.debug("business autoreply command skipped", exc_info=True)
    # Owner reply-keyword action: the owner replies to a message with a bare
    # keyword (translate/summarize/explain/context) → run the sandboxed no-tools
    # AI op on the replied-to message and DM the result to the owner PRIVATELY.
    # Replaces emoji reactions (which Telegram never delivers in business chats).
    try:
        if await reply_actions.run_business_reply(channel, msg, is_owner=is_owner, owner_id=owner_id):
            return
    except Exception:  # noqa: BLE001
        logger.debug("business reply-action skipped", exc_info=True)
    # Business-chat commands (ping/time/timer …) — anyone or owner per command;
    # result posted INTO the chat as the owner, the owner's trigger deleted.
    try:
        if await biz_commands.dispatch(channel, msg, is_owner=is_owner, owner_id=owner_id):
            return
    except Exception:  # noqa: BLE001
        logger.debug("business command skipped", exc_info=True)
    # A shared TikTok link gets a metadata card + Download/Analyse buttons (gated by
    # the 'download' policy). This is owner-facing DATA enrichment — still never a
    # command, and a no-op when the message has no TikTok link.
    try:
        from navig.telegram import tiktok_actions

        await tiktok_actions.offer_card(channel, chat_id, message_id, text, is_owner=is_owner)
    except Exception:  # noqa: BLE001
        logger.debug("tiktok offer_card skipped", exc_info=True)
    # A shared bare music link (Spotify/Apple/Deezer/…) gets the same track on every
    # platform (song.link). Owner-facing enrichment; a no-op without a bare music link
    # or when telegram.music_links.enabled is off.
    try:
        from navig.telegram import music_actions

        await music_actions.offer_links(channel, chat_id, message_id, text)
    except Exception:  # noqa: BLE001
        logger.debug("music offer_links skipped", exc_info=True)
    # Pro-mode auto-reply: if the owner activated a persona for this chat, answer
    # the counterparty AS the owner (human-like timing). No-op when inactive or
    # when the message is from the owner.
    try:
        if await autoreply.maybe_autoreply(channel, msg, is_owner=is_owner, owner_id=owner_id):
            return
    except Exception:  # noqa: BLE001
        logger.debug("business autoreply skipped", exc_info=True)
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
                "message_deleted",   # a registered notify type (navig.notify.types)
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


