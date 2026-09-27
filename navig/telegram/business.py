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
import os
import random
from datetime import datetime
from typing import Any

from . import autoreply, biz_commands, deletions, permissions, reply_actions

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


def _is_own_echo(channel, msg: dict) -> bool:
    """True when *msg* is one of the bot's OWN business sends echoed back by
    Telegram (``from`` = the bot, and/or ``sender_business_bot`` = the bot).

    Only the bot's own id qualifies — ``from.is_bot`` alone does NOT, because the
    owner also talks to *other* bots and those conversations belong in the
    catalog. With no token to compare against we cannot tell an echo from another
    bot, so nothing is skipped here and the ``is_bot`` branch keeps such a message
    DATA-only (no auto-reply) — the safe side either way."""
    own = _bot_id(channel)
    if not own:
        return False
    frm = msg.get("from") or {}
    if frm.get("id") is not None and str(frm.get("id")) == own:
        return True
    sbb = msg.get("sender_business_bot") or {}
    return sbb.get("id") is not None and str(sbb.get("id")) == own


def _extract_media(msg: dict) -> dict | None:
    """Photo/video/voice/… descriptor (with ``file_id``) for a business message,
    via the same extractor the regular catalog ingest uses; None for text-only."""
    try:
        from navig.gateway.channels.telegram_catalog_ingest import extract_media

        return extract_media(msg)
    except Exception:  # noqa: BLE001
        logger.debug("business media extract failed", exc_info=True)
        return None


# What a deleted message is described as in the owner's alert, per media kind,
# and the Bot API method + field that re-sends it by ``file_id``. ``video_note``
# and ``sticker`` take no caption.
MEDIA_ICON = {
    "photo": "📷", "video": "🎬", "animation": "🎞", "voice": "🎤", "audio": "🎵",
    "document": "📎", "video_note": "📹", "sticker": "🧩",
}
_MEDIA_SEND = {
    "photo": ("sendPhoto", "photo", True), "video": ("sendVideo", "video", True),
    "animation": ("sendAnimation", "animation", True), "voice": ("sendVoice", "voice", True),
    "audio": ("sendAudio", "audio", True), "document": ("sendDocument", "document", True),
    "video_note": ("sendVideoNote", "video_note", False), "sticker": ("sendSticker", "sticker", False),
}
_SNIPPET_MAX = 500
_LINES_MAX = 40      # the notify sink truncates at Telegram's 4096 anyway; this keeps it readable
_CAPTION_MAX = 1024  # Bot API limit


def media_label(kind: str | None) -> str:
    """"📷 photo" for a media kind — the ONE rendering of a file, shared by the
    deletion DM and `navig telegram business deleted` so the two surfaces cannot
    describe the same message differently."""
    k = kind or "media"
    return f"{MEDIA_ICON.get(k, '📎')} {k.replace('_', ' ')}"


def _person_name(user: dict) -> str:
    """Display name for a Telegram user: their name, else their handle.

    One order for people everywhere in this module — the alert's chat label and
    the stored ``sender_name`` disagreeing is what printed a hex handle next to a
    human name in the same DM."""
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p).strip()
    if name:
        return name
    if user.get("username"):
        return f"@{user['username']}"
    return str(user.get("id") or "")


def _edit_stamp(msg: dict) -> str:
    """ISO timestamp of an edit — Telegram's ``edit_date`` when present, else now."""
    try:
        if ts := msg.get("edit_date"):
            return datetime.fromtimestamp(int(ts)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _chat_label(chat: dict, cached_title: str | None = None) -> str:
    """Human name for the alert: a group title or the person's name — never the
    bare ``username`` first (a private chat has no ``title``, so the old
    ``title or username`` rendered a hex-looking handle instead of "Sam 🧢")."""
    name = chat.get("title") or " ".join(
        p for p in (chat.get("first_name"), chat.get("last_name")) if p
    ).strip()
    if not name:
        name = cached_title or ""
    if not name and chat.get("username"):
        name = f"@{chat['username']}"
    return name or str(chat.get("id"))


# Non-file content a message can carry: no ``file_id``, so ``extract_media`` sees
# nothing and the row lands with empty text. Naming them is what keeps a deleted
# location or poll from reading as "(no text)" — an answer that looks like a bug.
_CONTENT_KINDS: tuple[tuple[str, str, str], ...] = (
    ("story", "📖", "shared story"), ("location", "📍", "location"),
    ("venue", "📍", "venue"), ("contact", "👤", "contact"), ("poll", "📊", "poll"),
    ("dice", "🎲", "dice"), ("game", "🎮", "game"), ("invoice", "🧾", "invoice"),
    ("giveaway", "🎁", "giveaway"), ("gift", "🎁", "gift"), ("unique_gift", "🎁", "gift"),
    ("paid_media", "💳", "paid media"), ("checklist", "☑️", "checklist"),
    ("pinned_message", "📌", "pinned a message"),
    ("video_chat_started", "📹", "video chat started"),
    ("video_chat_ended", "📹", "video chat ended"),
)


def _content_kind(msg: dict) -> str | None:
    """Name the non-file content of *msg* (``story`` / ``poll`` / …), or None.

    Checked only after ``extract_media`` finds nothing, so a photo is never
    mislabelled by a field that merely rides along with it."""
    for field, _icon, _label in _CONTENT_KINDS:
        if msg.get(field) is not None:
            return field
    return None


def content_label(kind: str | None) -> str | None:
    """"📊 poll" for a non-file content kind, or None when it is not one.

    Public like :func:`media_label` and for the same reason: the deletion DM and
    `navig telegram business deleted` must describe one message identically."""
    for field, icon, label in _CONTENT_KINDS:
        if field == kind:
            return f"{icon} {label}"
    return None


def format_when(date_value: Any) -> str | None:
    """Short local time for a stored ``date``: ``17:18`` today, ``21 Sep 17:18``
    this year, ``21 Sep 2025`` before that.

    Accepts BOTH shapes the catalog holds — the business path stores a unix
    timestamp as a string, the regular ingest an ISO ``…Z`` string — because the
    alert reads rows written by either."""
    if date_value in (None, ""):
        return None
    dt: datetime | None = None
    raw = str(date_value).strip()
    try:
        dt = datetime.fromtimestamp(int(raw))
    except (TypeError, ValueError, OSError, OverflowError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            dt = parsed.astimezone() if parsed.tzinfo else parsed
        except ValueError:
            return None
    if dt is None:
        return None
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if dt.year == now.year:
        return dt.strftime("%-d %b %H:%M" if os.name != "nt" else "%#d %b %H:%M")
    return dt.strftime("%-d %b %Y" if os.name != "nt" else "%#d %b %Y")


def _seen_since(first_seen: str | None) -> str | None:
    """Human date this chat entered the catalog, for the not-seen explanation."""
    return format_when(first_seen)


def _deleted_by(cached: dict | None, chat: dict, owner_id: int | None, chat_label: str) -> str:
    """Who wrote the deleted message: "you", else a human name.

    For a PRIVATE chat the counterparty IS the chat, so the chat's own label wins
    over the stored ``sender_name`` — that is what rescues the rows written before
    ``_person_name``, which hold a raw handle. A group keeps the per-sender name,
    where the two are genuinely different people."""
    if not cached:
        return "?"
    if owner_id is not None and cached.get("sender_id") == owner_id:
        return "you"
    is_private = (chat.get("type") or "private") == "private" or chat.get("title") is None
    if is_private and chat_label:
        return chat_label
    return str(cached.get("sender_name") or "them")


def _describe_deleted(
    cached: dict | None,
    media: dict | None,
    *,
    who: str,
    message_id: Any = None,
    first_seen: str | None = None,
) -> str:
    """One alert line for a deleted message.

    Every branch carries WHEN the message was sent, because that single fact is
    what tells the three "no text" cases apart — and without it the operator
    reads a correct alert as a broken one (a deletion of yesterday's photo looks
    identical to today's photo going missing).

    Honest about why a line has no content: never seen at all · seen but
    non-file content · a row from before media was kept."""
    if cached is None:
        head = f"• msg {message_id} · not seen" if message_id is not None else "• not seen"
        since = _seen_since(first_seen)
        if since:
            return f"{head} — NAVIG has watched this chat since {since}, so this one is older"
        return f"{head} — NAVIG never cataloged this chat (it was offline, or the chat is new to it)"
    text = (cached.get("text") or "").strip()
    if len(text) > _SNIPPET_MAX:
        text = text[:_SNIPPET_MAX].rstrip() + "…"
    stamp = format_when(cached.get("date"))
    head = f"• {who}" + (f" · {stamp}" if stamp else "")
    if cached.get("edited_at"):
        head += " · edited"
    if media:
        kind = media.get("kind") or "media"
        head += f" · {media_label(kind)}"
        return f"{head} — {text}" if text else head
    if content := content_label(cached.get("content")):
        head += f" · {content}"
        return f"{head} — {text}" if text else head
    if text:
        return f"{head} — {text}"
    return f"{head} — (no copy kept: sent before NAVIG stored media, or a message type it cannot re-send)"


async def _resend_media(channel, chat_id, media: dict, caption: str) -> bool:
    """Re-send a cached media by ``file_id`` to the owner's DM. A file_id stays
    valid for the bot after the original message is deleted, so the owner gets
    the actual photo/voice/… back, not a placeholder. Best-effort: False when
    the send failed (logged) — the summary alert already named the media."""
    kind = media.get("kind") or ""
    file_id = media.get("file_id")
    spec = _MEDIA_SEND.get(kind)
    if not (spec and file_id and channel is not None and chat_id is not None):
        return False
    method, field, takes_caption = spec
    data: dict = {"chat_id": chat_id, field: file_id}
    if takes_caption and caption:
        data["caption"] = caption[:_CAPTION_MAX]
    try:
        res = await channel._api_call(method, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("deletion alert: could not re-send deleted %s to the owner: %s", kind, exc)
        return False
    if not res:
        logger.warning("deletion alert: Telegram rejected re-sending the deleted %s", kind)
        return False
    return True


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


def primary_owner() -> int | None:
    """The install's business owner, when there is no connection id in hand.

    ``resolve_owner(None)`` skipped the connection registry entirely (it looks up
    by id) and fell back to ``telegram.allowed_users`` alone — so code with no
    update to read a connection id from (the deletion digest) found no owner on an
    install whose owner is recorded only in the registry. A NAVIG install has one
    owner, so any registered connection names them."""
    try:
        conns = _cfg().get(CFG_CONNECTIONS, {}) or {}
        for rec in conns.values():
            if isinstance(rec, dict) and rec.get("owner_id") is not None:
                return int(rec["owner_id"])
    except Exception:  # noqa: BLE001
        pass
    return _owner_from_allowed()


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
    """Whether deletions are announced at all — i.e. the mode is not ``off``.

    Answered by :func:`deletions.mode`, the one source of truth. This read
    ``bool(cfg.get(...))`` on its own, which broke twice: ``navig config set …
    deletion_alert false`` stores the STRING "false", which ``bool()`` calls True,
    so status reported ON for an operator who had switched it off; and once a mode
    was set explicitly, the boolean no longer decided anything at all."""
    return deletions.mode() != "off"


def set_deletion_alert(value: bool) -> None:
    """The original on/off switch (``navig telegram business alerts on|off``).

    It used to write only the legacy boolean — and once ``mode`` had been set
    explicitly (``deletions mode …``, or the digest card's Quiet button) the mode
    wins, so ``alerts off`` silently did nothing. It now drives the mode: off means
    ``off``; on restores the default ``digest`` if alerts were off, and otherwise
    keeps an explicit choice (an operator on ``instant`` stays on ``instant``)."""
    if not value:
        deletions.set_mode("off")
    elif deletions.mode() == "off":
        deletions.set_mode(deletions.DEFAULT_MODE)
    else:
        deletions.set_mode(deletions.mode())   # keeps the legacy boolean in step


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
    # updates (from = the bot, and/or ``sender_business_bot`` = the bot). Without
    # this, pro-mode auto-reply would answer its own replies forever. Skip anything
    # the bot itself sent.
    if _is_own_echo(channel, msg):
        return
    # Any OTHER bot is a counterparty the owner talks to (@some_bot chats). Its
    # messages are DATA worth keeping — the deletion alert reads them back — but
    # never a party we act on: no auto-reply (two bots answering each other never
    # stops), no commands, no enrichment cards. The guard used to drop every
    # ``is_bot`` sender here, so whole bot conversations were cataloged one-sided
    # and every deleted bot message came back as "(content was not cached)".
    is_other_bot = bool(frm.get("is_bot"))
    owner_id = resolve_owner(msg.get("business_connection_id"))
    is_owner = bool(owner_id and sender_id == owner_id)
    text = msg.get("text") or msg.get("caption") or ""
    # Media descriptor (photo/video/voice/sticker/…): the file_id is what lets the
    # deletion alert re-send the actual content to the owner instead of a
    # placeholder. A caption-less photo used to be stored as text="" and nothing
    # else, so its deletion alert read "(content was not cached)" for a message
    # NAVIG had in fact seen.
    media = _extract_media(msg)
    # A message with neither text nor a file (a location, a poll, a shared story)
    # would otherwise be stored as a blank row and read back as "(no text)".
    content = None if media else _content_kind(msg)
    # Routing metadata only -- NOT the message body. This logged 50 chars of every
    # private business message at INFO, so `~/.navig/logs/gateway.log` accumulated a
    # plaintext transcript of the operator's conversations with third parties who
    # never consented to it, in a file that gets tailed during debugging and pasted
    # into issues. It bought nothing: the text is already persisted deliberately by
    # `upsert_message` below, which is the catalog feature and the right place to
    # read it. `integrations/telegram_voice_bot.py` sets the precedent -- log the
    # LENGTH, not the content.
    logger.info(
        "business message: chat=%s from=%s owner=%s is_owner=%s bot=%s chars=%d media=%s content=%s",
        chat_id, sender_id, owner_id, is_owner, is_other_bot, len(text),
        (media or {}).get("kind") or "-", content or "-",
    )
    try:
        store = _store()
        store.upsert_room(chat_id, type="business",
                          title=chat.get("title") or chat.get("first_name") or "")
        media_id: int | None = None
        if media:
            media_id = store.upsert_media(
                chat_id, message_id=message_id,
                file_id=media.get("file_id"), file_unique_id=media.get("file_unique_id"),
                kind=media.get("kind"), mime=media.get("mime"), size=media.get("size"),
                filename=media.get("filename"),
            ) or None
        raw = {"business": True, "from_owner": is_owner,
               "connection_id": msg.get("business_connection_id")}
        if media:
            raw["media"] = media.get("kind")
            if emoji := (msg.get("sticker") or {}).get("emoji"):
                raw["emoji"] = emoji
        if content:
            raw["content"] = content
        store.upsert_message(
            chat_id, message_id,
            sender_id=sender_id,
            # Person first, handle last — the SAME order the chat label uses. The
            # username came first here, so a deletion alert introduced the
            # counterparty by their raw handle ("sam_exam…") while the very
            # next line of the same DM called the chat "Sam 🧢".
            sender_name=_person_name(frm),
            date=str(msg.get("date") or ""),
            # `or None`: an empty string OVERWRITES via upsert's COALESCE, so a
            # caption-less edit or a re-delivered update would erase text the
            # deletion alert is the last reader of. NULL preserves it.
            text=text or None,
            kind="business",
            media_ref=media_id,
            # A real timestamp — `edited_at` is a time everywhere else in this
            # table (`update_message_text` writes `_utcnow()`), and "yes" made the
            # column unsortable and unreadable by every other consumer.
            edited_at=(_edit_stamp(msg) if edited else None),
            raw=raw,
        )
    except Exception:  # noqa: BLE001
        logger.debug("business message catalog failed", exc_info=True)
    if is_other_bot:
        return  # cataloged as DATA; a bot never reaches the action pipeline below
    # Owner pro-mode control ("role … on/off") — owner-only; deletes the command
    # and toggles AI persona auto-reply for this conversation.
    try:
        if await autoreply.handle_command(channel, msg, is_owner=is_owner, owner_id=owner_id):
            return
    except Exception:  # noqa: BLE001
        logger.debug("business autoreply command skipped", exc_info=True)
    # Owner reply-keyword action: the owner replies to a message with a bare
    # keyword (translate/summarize/explain/context) → run the sandboxed no-tools
    # AI op on the replied-to message and post the result INTO the chat AS the owner
    # (save stays a private DM). Replaces emoji reactions (never delivered here).
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




async def render_deletion_detail(
    channel, chat: dict, ids: list, *, owner_id: int | None = None,
) -> tuple[str, list[tuple[dict, str]]]:
    """Build the human report for a set of deleted ids in one chat.

    Returns ``(body, media_to_resend)`` and sends nothing, so the instant alert
    and the digest's "Show" button render identically — the button producing a
    different answer from the alert it replaces is the failure mode this split
    exists to prevent."""
    store = _store()
    chat_id = chat.get("id")
    cached_title = None
    try:
        cached_title = (store.get_room(chat_id) or {}).get("title")
    except Exception:  # noqa: BLE001
        cached_title = None
    chat_label = _chat_label(chat, cached_title)

    # Asked once per chat, not per id: it is the same answer for every message
    # here, and it is only consulted when a message is missing.
    first_seen: str | None = None
    try:
        first_seen = store.first_message_at(chat_id)
    except Exception:  # noqa: BLE001
        first_seen = None

    lines: list[str] = []
    media_to_resend: list[tuple[dict, str]] = []   # (media row, caption)
    for mid in ids:
        cached = None
        try:
            cached = store.get_message_by_ref(chat_id, mid)
        except Exception:  # noqa: BLE001
            cached = None
        media = None
        if cached and cached.get("media_ref"):
            try:
                media = store.get_media(int(cached["media_ref"]))
            except Exception:  # noqa: BLE001
                media = None
        who = _deleted_by(cached, chat, owner_id, chat_label)
        lines.append(_describe_deleted(cached, media, who=who,
                                       message_id=mid, first_seen=first_seen))
        if media and media.get("file_id"):
            cap = f"🗑 Deleted in {chat_label} ({who})"
            if text := (cached.get("text") or "").strip():
                cap += f"\n{text}"
            media_to_resend.append((media, cap))

    if len(lines) > _LINES_MAX:   # a whole-chat clear: keep the message readable
        lines = lines[:_LINES_MAX] + [f"• … and {len(lines) - _LINES_MAX} more"]
    header = "🗑 Message deleted" if len(ids) == 1 else f"🗑 {len(ids)} messages deleted"
    body = f"{header}\nIn {chat_label}:\n" + "\n".join(lines)
    return body, media_to_resend


async def deliver_deletion_detail(
    channel, chat: dict, ids: list, *, owner_id: int | None = None,
    target: str | None = None, check_prefs: bool = True,
) -> bool:
    """Render and deliver one chat's deletion detail, then re-send its files.

    ``check_prefs`` is False when the operator ASKED for this (the Show button):
    quiet hours mute the unprompted alert, not an answer to a tap."""
    body, media_to_resend = await render_deletion_detail(
        channel, chat, ids, owner_id=owner_id)
    if check_prefs:
        allowed, why = deletions.should_notify()
        if not allowed:
            # Recorded, deliberately not announced. INFO rather than silence: "I
            # chose not to tell you" is a different fact from "nothing happened",
            # and the rows are still there (navig telegram business deleted).
            logger.info("deletion alert suppressed (%s): chat=%s ids=%d",
                        why, chat.get("id"), len(ids))
            return False
    if target is None:
        target = await deletions.resolve_target(owner_id)
    if not await deletions.send_detail(channel, body, target=target):
        return False
    # The files follow the summary that announced them, into the same chat — so a
    # log chat keeps the evidence together with its report.
    failed = 0
    for media, cap in media_to_resend:
        if not await _resend_media(channel, target, media, cap):
            failed += 1
    # The summary PROMISED a photo/voice that then did not arrive. Saying so is the
    # whole doctrine of this file: an alert that silently delivers less than it
    # announced trains the operator to distrust the ones that work.
    if failed and target is not None and channel is not None:
        kinds = ", ".join(sorted({(m.get("kind") or "media") for m, _ in media_to_resend}))
        try:
            await channel._api_call("sendMessage", {
                "chat_id": target,
                "text": (f"⚠️ Couldn't re-send {failed} of {len(media_to_resend)} deleted "
                         f"file(s) ({kinds}) — Telegram no longer serves that file id. "
                         f"The summary above is all that survives."),
            })
        except Exception:  # noqa: BLE001
            logger.debug("deletion alert: resend-failure note not delivered", exc_info=True)
    return True


async def handle_deleted_business_messages(channel, payload: dict) -> None:
    """A deletion in a business conversation: record it, then decide who hears.

    Three independent switches, all in :mod:`navig.telegram.deletions`:

    * **record** — the catalog row is the ONLY surviving trace of a deleted
      message, so writing it down is a data decision. Off means no trace at all.
    * **mode** — ``instant`` (a report per event, the original behaviour),
      ``digest`` (one "N deleted · Show" card per window — the default, because
      instant floods a busy account with a message AND a file per deletion), or
      ``off`` (keep the record, say nothing).
    * **target** — the owner's DM, or a separate log chat.

    Recording happens FIRST and unconditionally of the mode: the digest reads the
    catalog at flush time rather than holding content in memory, so a restart
    mid-window loses nothing.
    """
    if not permissions.business_enabled():
        return
    chat = payload.get("chat") or {}
    chat_id = chat.get("id")
    ids = [m for m in (payload.get("message_ids") or []) if m is not None]
    if chat_id is None or not ids:
        return
    if not deletions.record_enabled():
        return  # deletion watching switched off entirely: no row, no alert
    owner_id = resolve_owner(payload.get("business_connection_id"))

    store = _store()
    for mid in ids:
        try:
            store.mark_message_deleted(chat_id, mid)
        except Exception:  # noqa: BLE001
            logger.debug("mark_message_deleted failed for %s/%s", chat_id, mid, exc_info=True)

    how = "off" if deletions.is_muted(chat_id) else deletions.mode()
    logger.info("business deletion: chat=%s ids=%d mode=%s", chat_id, len(ids), how)
    if how == "off":
        return
    if how == "digest":
        deletions.arm_digest(channel)
        return
    await deliver_deletion_detail(channel, chat, ids, owner_id=owner_id)
