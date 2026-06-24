"""Telegram network manager — deck API.

Browse the bot's rooms (groups/channels), catalog & analyse their messages and
media, quick-search, and post / edit / delete messages through the existing
Telegram send path.

The live ``TelegramChannel`` is reached via ``gateway.channels["telegram"]``
(``request.app.get("gateway")``), with the messaging adapter's injected bot as
a fallback. That channel exposes ``send_message``/``edit_message``/
``delete_message``/``_api_call`` used here for post/edit/delete + room sync.

Routes (all under /api/deck):
  GET    /telegram/rooms                       list rooms (?type= ?admin=1)
  GET    /telegram/rooms/{id}                   room metadata
  POST   /telegram/rooms/{id}/refresh           re-sync meta via getChat
  GET    /telegram/rooms/{id}/messages          ?kind= ?q= ?limit= ?before=
  GET    /telegram/rooms/{id}/media             ?kind=
  POST   /telegram/rooms/{id}/post              body: {text, reply_to?}
  GET    /telegram/media/{id}                   media + analysis
  POST   /telegram/media/{id}/analyze           (re)run analysis
  GET    /telegram/search                       ?q= ?chat_id=
  PATCH  /telegram/messages/{id}                body: {text, confirm}
  DELETE /telegram/messages/{id}                body/query: {confirm}
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _chat_id(request: "web.Request") -> int | None:
    try:
        return int(request.match_info["id"])
    except (KeyError, ValueError):
        return None


def _store():
    from navig.store.telegram_catalog import get_telegram_catalog

    return get_telegram_catalog()


def _channel(request: "web.Request") -> Any | None:
    """Return the live ``TelegramChannel`` from the gateway, or None.

    The live channel (with ``_api_call``/``edit_message``/``send_photo``/
    ``bot_token``/``_session``) lives at ``gateway.channels["telegram"]`` — not
    on the messaging adapter, whose ``_bot`` is the lower-level bot object.
    """
    try:
        gw = request.app.get("gateway") if hasattr(request, "app") else None
        ch = gw.channels.get("telegram") if gw is not None and hasattr(gw, "channels") else None
        if ch is not None:
            return ch
    except Exception as exc:  # noqa: BLE001
        logger.debug("telegram channel via gateway unavailable: %s", exc)
    # Fallback: the messaging adapter's injected bot (may lack manager methods).
    try:
        from navig.messaging.adapter_registry import get_adapter_registry

        adapter = get_adapter_registry().get("telegram")
        return getattr(adapter, "_bot", None) if adapter else None
    except Exception:  # noqa: BLE001
        return None


def _truthy(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


# ─── Rooms ───────────────────────────────────────────────────────────────────


async def handle_rooms_list(request: "web.Request") -> "web.Response":
    try:
        room_type = request.query.get("type") or None
        admin_only = _truthy(request.query.get("admin", ""))
        return _ok({"rooms": _store().list_rooms(type=room_type, admin_only=admin_only)})
    except Exception as exc:
        logger.exception("telegram rooms list failed")
        return _err(str(exc))


async def handle_room_get(request: "web.Request") -> "web.Response":
    cid = _chat_id(request)
    if cid is None:
        return _err("invalid room id", status=400)
    room = _store().get_room(cid)
    return _ok({"room": room}) if room else _err("room not found", status=404)


async def handle_room_refresh(request: "web.Request") -> "web.Response":
    cid = _chat_id(request)
    if cid is None:
        return _err("invalid room id", status=400)
    channel = _channel(request)
    if channel is None:
        return _err("telegram bot not running", status=503)
    try:
        from navig.gateway.channels.telegram_catalog_ingest import sync_room_meta

        room = await sync_room_meta(channel, cid)
        return _ok({"room": room})
    except Exception as exc:
        logger.exception("telegram room refresh failed")
        return _err(str(exc), status=502)


async def handle_room_messages(request: "web.Request") -> "web.Response":
    cid = _chat_id(request)
    if cid is None:
        return _err("invalid room id", status=400)
    try:
        kind = request.query.get("kind") or None
        q = request.query.get("q") or None
        limit = int(request.query.get("limit", 100))
        before = request.query.get("before")
        before_id = int(before) if before else None
        msgs = _store().list_messages(cid, kind=kind, q=q, limit=limit, before_id=before_id)
        return _ok({"messages": msgs, "count": len(msgs)})
    except Exception as exc:
        logger.exception("telegram room messages failed")
        return _err(str(exc))


async def handle_room_media(request: "web.Request") -> "web.Response":
    cid = _chat_id(request)
    if cid is None:
        return _err("invalid room id", status=400)
    try:
        kind = request.query.get("kind") or None
        media = _store().list_media(cid, kind=kind)
        return _ok({"media": media, "count": len(media)})
    except Exception as exc:
        logger.exception("telegram room media failed")
        return _err(str(exc))


async def handle_room_post(request: "web.Request") -> "web.Response":
    cid = _chat_id(request)
    if cid is None:
        return _err("invalid room id", status=400)
    body = await _body(request)
    text = (body.get("text") or "").strip()
    if not text:
        return _err("'text' is required", status=400)
    channel = _channel(request)
    if channel is None:
        return _err("telegram bot not running", status=503)
    try:
        reply_to = body.get("reply_to")
        msg = await channel.send_message(
            cid, text, reply_to_message_id=int(reply_to) if reply_to else None
        )
        message_id = (msg or {}).get("message_id") if isinstance(msg, dict) else getattr(msg, "message_id", None)
        return _ok({"sent": msg is not None, "message_id": message_id})
    except Exception as exc:
        logger.exception("telegram post failed")
        return _err(str(exc), status=502)


# ─── Media ───────────────────────────────────────────────────────────────────


async def handle_media_get(request: "web.Request") -> "web.Response":
    try:
        mid = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid media id", status=400)
    media = _store().get_media(mid)
    return _ok({"media": media}) if media else _err("media not found", status=404)


async def handle_media_analyze(request: "web.Request") -> "web.Response":
    try:
        mid = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid media id", status=400)
    channel = _channel(request)
    if channel is None:
        return _err("telegram bot not running", status=503)
    try:
        from navig.gateway.channels.telegram_catalog_analyzer import analyze_media

        result = await analyze_media(channel, mid)
        return _ok(result)
    except Exception as exc:
        logger.exception("telegram media analyze failed")
        return _err(str(exc), status=502)


# ─── Search ──────────────────────────────────────────────────────────────────


async def handle_search(request: "web.Request") -> "web.Response":
    q = (request.query.get("q") or "").strip()
    if not q:
        return _ok({"results": []})
    try:
        chat_id = request.query.get("chat_id")
        limit = int(request.query.get("limit", 50))
        results = _store().search(q, chat_id=int(chat_id) if chat_id else None, limit=limit)
        return _ok({"results": results, "count": len(results)})
    except Exception as exc:
        logger.exception("telegram search failed")
        return _err(str(exc))


# ─── Edit / Delete (confirm-gated) ───────────────────────────────────────────


async def handle_message_edit(request: "web.Request") -> "web.Response":
    try:
        local_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid message id", status=400)
    body = await _body(request)
    if not _truthy(body.get("confirm", "")):
        return _err("confirmation required", status=412)
    text = (body.get("text") or "").strip()
    if not text:
        return _err("'text' is required", status=400)
    store = _store()
    msg = store.get_message(local_id)
    if not msg:
        return _err("message not found", status=404)
    channel = _channel(request)
    if channel is None:
        return _err("telegram bot not running", status=503)
    try:
        result = await channel.edit_message(msg["chat_id"], msg["message_id"], text)
        if result is None:
            return _err("edit rejected by Telegram", status=502)
        store.update_message_text(local_id, text)
        return _ok({"edited": True})
    except Exception as exc:
        logger.exception("telegram edit failed")
        return _err(str(exc), status=502)


async def handle_message_delete(request: "web.Request") -> "web.Response":
    try:
        local_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return _err("invalid message id", status=400)
    body = await _body(request)
    confirm = _truthy(body.get("confirm", "")) or _truthy(request.query.get("confirm", ""))
    if not confirm:
        return _err("confirmation required", status=412)
    store = _store()
    msg = store.get_message(local_id)
    if not msg:
        return _err("message not found", status=404)
    channel = _channel(request)
    if channel is None:
        return _err("telegram bot not running", status=503)
    try:
        ok = await channel.delete_message(msg["chat_id"], msg["message_id"])
        if not ok:
            return _err("delete rejected by Telegram", status=502)
        store.mark_message_deleted(msg["chat_id"], msg["message_id"])
        return _ok({"deleted": True})
    except Exception as exc:
        logger.exception("telegram delete failed")
        return _err(str(exc), status=502)


# ─── Registration ────────────────────────────────────────────────────────────


def register(app: "web.Application") -> None:
    """Attach the Telegram-manager routes to the deck app."""
    app.router.add_get("/api/deck/telegram/rooms", handle_rooms_list)
    app.router.add_get("/api/deck/telegram/rooms/{id}", handle_room_get)
    app.router.add_post("/api/deck/telegram/rooms/{id}/refresh", handle_room_refresh)
    app.router.add_get("/api/deck/telegram/rooms/{id}/messages", handle_room_messages)
    app.router.add_get("/api/deck/telegram/rooms/{id}/media", handle_room_media)
    app.router.add_post("/api/deck/telegram/rooms/{id}/post", handle_room_post)
    app.router.add_get("/api/deck/telegram/media/{id}", handle_media_get)
    app.router.add_post("/api/deck/telegram/media/{id}/analyze", handle_media_analyze)
    app.router.add_get("/api/deck/telegram/search", handle_search)
    app.router.add_patch("/api/deck/telegram/messages/{id}", handle_message_edit)
    app.router.add_delete("/api/deck/telegram/messages/{id}", handle_message_delete)
