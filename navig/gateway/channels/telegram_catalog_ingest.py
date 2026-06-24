"""
Telegram catalog ingestion — persists the bot's inbound updates into the
:class:`navig.store.telegram_catalog.TelegramCatalogStore` so the deck
"Telegram network manager" can browse / analyse / search them.

Self-contained on purpose: the runtime ``TelegramChannel`` does not inherit
its mixins (MRO is just ``[TelegramChannel, object]``), so these are plain
module functions that take the live channel and call ``channel._api_call``
directly.

Gated by config ``telegram.catalog.enabled`` (default off) so it is a pure
no-op for users who never open the network manager. Best-effort throughout —
ingestion must never break message dispatch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Update keys that carry a message we want to catalog.
_MESSAGE_KEYS = ("message", "channel_post", "edited_message", "edited_channel_post")

# Extra allowed_updates the catalog needs (merged into the channel's defaults).
CATALOG_ALLOWED_UPDATES = ["channel_post", "edited_message", "edited_channel_post"]


def merge_allowed_updates(base: list[str]) -> list[str]:
    """Return *base* plus the catalog's extra update types, de-duplicated."""
    out = list(base)
    for u in CATALOG_ALLOWED_UPDATES:
        if u not in out:
            out.append(u)
    return out


def catalog_config() -> dict[str, Any]:
    """Read ``telegram.catalog`` config; returns sane defaults on any error."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        cfg = (cm.global_config or {}).get("telegram", {}).get("catalog", {})
        if isinstance(cfg, dict):
            return cfg
    except Exception:  # noqa: BLE001 — config unavailable; use defaults
        pass
    return {}


def _coerce_bool(v: Any, default: bool = False) -> bool:
    """Tolerant bool: handles real bools and the string values `navig config set`
    stores (it writes the CLI argument verbatim, so ``"false"`` would otherwise
    read as truthy)."""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# Cache the "a Telegram token is configured" probe — it can touch the vault, and
# ingest_update() calls catalog_enabled() on every update.
_token_present: bool | None = None


def _telegram_token_present() -> bool:
    global _token_present
    if _token_present is None:
        try:
            from navig.messaging.secrets import resolve_telegram_bot_token

            _token_present = bool((resolve_telegram_bot_token() or "").strip())
        except Exception:  # noqa: BLE001
            _token_present = False
    return _token_present


def catalog_enabled() -> bool:
    """Catalog is **auto-on once a Telegram bot token is configured**, so it works
    the moment a key is set — unless ``telegram.catalog.enabled`` is set explicitly
    (e.g. ``navig config set telegram.catalog.enabled false`` to opt out)."""
    cfg = catalog_config()
    if "enabled" in cfg:
        return _coerce_bool(cfg["enabled"])
    return _telegram_token_present()


def _ts_to_iso(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _sender_name(message: dict) -> str | None:
    frm = message.get("from") or {}
    if frm:
        name = " ".join(p for p in (frm.get("first_name"), frm.get("last_name")) if p).strip()
        return name or frm.get("username") or (str(frm.get("id")) if frm.get("id") else None)
    # Channel posts have no `from`; fall back to a signature or the channel title.
    return message.get("author_signature") or (message.get("sender_chat") or {}).get("title")


def extract_media(message: dict) -> dict[str, Any] | None:
    """Pull a single media descriptor out of a Telegram message, or None."""
    if photos := message.get("photo"):
        best = max(photos, key=lambda p: p.get("file_size", 0))
        return {
            "kind": "photo",
            "file_id": best.get("file_id"),
            "file_unique_id": best.get("file_unique_id"),
            "mime": "image/jpeg",
            "size": best.get("file_size"),
            "filename": f"photo_{best.get('file_unique_id', 'x')}.jpg",
        }
    for kind in ("video", "animation", "voice", "audio", "document", "video_note", "sticker"):
        obj = message.get(kind)
        if not obj:
            continue
        return {
            "kind": kind,
            "file_id": obj.get("file_id"),
            "file_unique_id": obj.get("file_unique_id"),
            "mime": obj.get("mime_type"),
            "size": obj.get("file_size"),
            "filename": obj.get("file_name") or f"{kind}_{obj.get('file_unique_id', 'x')}",
        }
    return None


async def ingest_update(channel: Any, update: dict) -> None:
    """Persist a message-bearing update into the catalog (best-effort)."""
    if not catalog_enabled():
        return

    message: dict | None = None
    is_edit = False
    for key in _MESSAGE_KEYS:
        if msg := update.get(key):
            message = msg
            is_edit = key.startswith("edited_")
            break
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    from navig.store.telegram_catalog import get_telegram_catalog

    store = get_telegram_catalog()
    date_iso = _ts_to_iso(message.get("date"))

    store.upsert_room(
        chat_id,
        type=chat.get("type"),
        title=chat.get("title") or chat.get("username") or (chat.get("first_name") or None),
        username=chat.get("username"),
        last_message_at=date_iso,
    )

    media = extract_media(message)
    media_id: int | None = None
    if media:
        media_id = store.upsert_media(
            chat_id,
            message_id=message.get("message_id"),
            file_id=media.get("file_id"),
            file_unique_id=media.get("file_unique_id"),
            kind=media.get("kind"),
            mime=media.get("mime"),
            size=media.get("size"),
            filename=media.get("filename"),
        )

    text = message.get("text") or message.get("caption") or ""
    edited_at = _ts_to_iso(message.get("edit_date")) if is_edit else None
    store.upsert_message(
        chat_id,
        message.get("message_id"),
        sender_id=(message.get("from") or {}).get("id"),
        sender_name=_sender_name(message),
        date=date_iso,
        text=text or None,
        reply_to=(message.get("reply_to_message") or {}).get("message_id"),
        media_ref=media_id,
        kind=(media.get("kind") if media else "text"),
        edited_at=edited_at,
        raw=message,
    )

    # Auto-analyse new media (not on edits — media doesn't change on edit).
    if media_id and not is_edit and _coerce_bool(catalog_config().get("auto_analyze", True), default=True):
        try:
            from navig.gateway.channels.telegram_catalog_analyzer import schedule_analysis

            schedule_analysis(channel, media_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not schedule media analysis: %s", exc)

    # Live deck refresh.
    await _emit_event(chat_id)


async def _emit_event(chat_id: int) -> None:
    try:
        from navig.gateway.system_events import get_system_events

        queue = get_system_events()
        if queue is not None:
            await queue.emit("telegram_catalog_update", {"chat_id": chat_id})
    except Exception:  # noqa: BLE001 — events optional
        pass


async def sync_room_meta(channel: Any, chat_id: int) -> dict[str, Any] | None:
    """Refresh a room's metadata via getChat / member count / bot membership.

    Populates admin status, post/delete permissions and (for groups) a best-effort
    privacy-mode flag. Returns the updated room dict.
    """
    from navig.store.telegram_catalog import get_telegram_catalog

    store = get_telegram_catalog()

    chat = await channel._api_call("getChat", {"chat_id": chat_id})
    if not chat:
        return store.get_room(chat_id)

    member_count = None
    mc = await channel._api_call("getChatMemberCount", {"chat_id": chat_id})
    try:
        member_count = int(mc) if mc is not None else None
    except (TypeError, ValueError):
        member_count = None

    bot_is_admin = None
    can_post = None
    can_delete = None
    bot_id = getattr(channel, "bot_id", None) or getattr(channel, "_bot_id", None)
    if not bot_id:
        me = await channel._api_call("getMe")
        bot_id = (me or {}).get("id")
    if bot_id:
        member = await channel._api_call(
            "getChatMember", {"chat_id": chat_id, "user_id": bot_id}
        )
        if member:
            status = member.get("status")
            bot_is_admin = status in ("administrator", "creator")
            if status == "creator":
                can_post = can_delete = True
            elif status == "administrator":
                can_post = bool(member.get("can_post_messages", chat.get("type") != "channel"))
                can_delete = bool(member.get("can_delete_messages", False))

    # Privacy mode: a bot in a group with privacy on only sees commands/replies.
    # The Bot API doesn't expose the toggle directly; admins implicitly see all.
    privacy_ok = True if bot_is_admin else None

    store.upsert_room(
        chat_id,
        type=chat.get("type"),
        title=chat.get("title") or chat.get("username") or chat.get("first_name"),
        username=chat.get("username"),
        member_count=member_count,
        bot_is_admin=bot_is_admin,
        can_post=can_post,
        can_delete=can_delete,
        privacy_ok=privacy_ok,
        touch_sync=True,
    )
    return store.get_room(chat_id)
