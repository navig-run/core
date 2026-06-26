"""Unified messaging — threads, contacts, send across adapters.

Backed by `navig.store.threads.ThreadStore` and `navig.store.contacts.ContactStore`
(the same stores the Telegram messaging mixin uses). Sending defers to the
routing engine in `navig.commands.dispatch`.

Routes:
  GET  /api/deck/messages/threads               list threads (filter ?adapter=)
  GET  /api/deck/messages/threads/{id}          thread metadata
  GET  /api/deck/messages/contacts              list contacts
  POST /api/deck/messages/send                  body: {target, body, network?}
"""

from __future__ import annotations

import dataclasses
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


async def _read_body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _serialize(obj: Any) -> Any:
    """Recursively turn dataclasses (Thread/Contact/Route) into plain dicts."""
    if dataclasses.is_dataclass(obj):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


# ─── Threads ─────────────────────────────────────────────────────────────────


async def handle_deck_messages_threads_list(request: "web.Request") -> "web.Response":
    adapter = request.query.get("adapter") or None
    status_filter = request.query.get("status") or None
    try:
        limit = max(1, min(500, int(request.query.get("limit", 100))))
    except ValueError:
        limit = 100
    try:
        from navig.store.threads import get_thread_store  # type: ignore[import]
        store = get_thread_store()
        threads = store.list_threads(adapter=adapter, status=status_filter, limit=limit)

        # Count by adapter for header chips
        by_adapter: dict[str, int] = {}
        for t in threads:
            by_adapter[t.adapter] = by_adapter.get(t.adapter, 0) + 1

        return _ok({
            "count": len(threads),
            "by_adapter": by_adapter,
            "threads": [_serialize(t) for t in threads],
        })
    except Exception as exc:
        logger.exception("threads list failed")
        return _err(str(exc))


async def handle_deck_messages_thread_detail(request: "web.Request") -> "web.Response":
    tid_raw = request.match_info.get("thread_id", "")
    try:
        tid = int(tid_raw)
    except ValueError:
        return _err("invalid thread id", status=400)
    try:
        from navig.store.threads import get_thread_store  # type: ignore[import]
        store = get_thread_store()
        # The store has `_read_one` etc but the public way is to scan list_threads.
        # For now fetch by listing then matching.
        threads = store.list_threads(limit=500)
        match = next((t for t in threads if t.id == tid), None)
        if match is None:
            return _err(f"thread {tid} not found", status=404)
        return _ok(_serialize(match))
    except Exception as exc:
        logger.exception("thread detail failed")
        return _err(str(exc))


# ─── Contacts ────────────────────────────────────────────────────────────────


async def handle_deck_messages_contacts(request: "web.Request") -> "web.Response":
    q = (request.query.get("q") or "").strip()
    try:
        limit = max(1, min(500, int(request.query.get("limit", 200))))
    except ValueError:
        limit = 200
    try:
        from navig.store.contacts import get_contact_store  # type: ignore[import]
        store = get_contact_store()
        if q:
            contacts = store.search(q, limit=limit)
        else:
            contacts = store.list_contacts(limit=limit)
        return _ok({
            "count": len(contacts),
            "contacts": [_serialize(c) for c in contacts],
        })
    except Exception as exc:
        logger.exception("contacts list failed")
        return _err(str(exc))


async def handle_deck_messages_contact_add(request: "web.Request") -> "web.Response":
    """Add (or upsert routes onto) a contact. Body: {alias, display_name?,
    phone?, network?, routes?[], default_network?}."""
    body = await _read_body(request)
    alias = (body.get("alias") or "").strip()
    if not alias:
        return _err("'alias' is required", status=400)
    display_name = (body.get("display_name") or "").strip()
    routes = [str(r) for r in (body.get("routes") or [])]
    phone = (body.get("phone") or "").strip()
    network = (body.get("network") or "sms").strip().lower()
    try:
        from navig.store.contacts import get_contact_store, normalize_phone

        if phone:
            routes.append(f"{network}:{normalize_phone(phone)}")
        default_network = body.get("default_network") or (network if phone else None)
        store = get_contact_store()
        if store.resolve_alias(alias):  # upsert routes onto an existing contact
            for r in routes:
                store.add_route(alias, r)
            if display_name:
                store.update_contact(alias, display_name=display_name)
            contact = store.resolve_alias(alias)
        else:
            contact = store.add_contact(
                alias, display_name, routes=routes or None, default_network=default_network
            )
        return _ok(_serialize(contact), status=201)
    except Exception as exc:
        logger.exception("contact add failed")
        return _err(str(exc), status=400)


async def handle_deck_messages_contact_update(request: "web.Request") -> "web.Response":
    """Update a contact. Body: {display_name?, default_network?, phone?+network?,
    add_route?, remove_route?}."""
    alias = request.match_info.get("alias", "")
    body = await _read_body(request)
    try:
        from navig.store.contacts import get_contact_store, normalize_phone

        store = get_contact_store()
        if not store.resolve_alias(alias):
            return _err("contact not found", status=404)
        if body.get("display_name") is not None or "default_network" in body:
            store.update_contact(
                alias,
                display_name=body.get("display_name"),
                default_network=body.get("default_network", ...),
            )
        add_route = body.get("add_route")
        if not add_route and body.get("phone"):
            add_route = f"{(body.get('network') or 'sms').strip().lower()}:{normalize_phone(body['phone'])}"
        if add_route:
            store.add_route(alias, str(add_route))
        if body.get("remove_route"):
            store.remove_route(alias, str(body["remove_route"]))
        return _ok(_serialize(store.resolve_alias(alias)))
    except Exception as exc:
        logger.exception("contact update failed")
        return _err(str(exc), status=400)


async def handle_deck_messages_contact_delete(request: "web.Request") -> "web.Response":
    alias = request.match_info.get("alias", "")
    try:
        from navig.store.contacts import get_contact_store

        ok = get_contact_store().remove_contact(alias)
        return _ok({"deleted": True}) if ok else _err("contact not found", status=404)
    except Exception as exc:
        logger.exception("contact delete failed")
        return _err(str(exc))


# ─── Send ────────────────────────────────────────────────────────────────────


async def handle_deck_messages_send(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    target = (body.get("target") or "").strip()
    msg = (body.get("body") or "").strip()
    network = (body.get("network") or "").strip() or None
    if not target or not msg:
        return _err("'target' and 'body' are required", status=400)
    try:
        from navig.commands.dispatch import RoutingEngine  # type: ignore[import]
        from navig.store.contacts import get_contact_store  # type: ignore[import]
        from navig.store.threads import get_thread_store  # type: ignore[import]
        from navig.messaging.registry import get_adapter_registry  # type: ignore[import]
        engine = RoutingEngine(get_contact_store(), get_thread_store(), get_adapter_registry())
        # The engine resolves target → adapter → dispatches; signature is best-effort
        # since the routing engine surface is internal. Surface a tidy receipt.
        try:
            decision = engine.resolve(target, network=network)
            adapter = get_adapter_registry().get(decision.adapter_name)
            receipt = await adapter.send(decision.resolved_target, msg)
            return _ok({
                "ok": bool(getattr(receipt, "ok", False)),
                "adapter": decision.adapter_name,
                "target": target,
                "id": getattr(receipt, "id", None) or "",
                "error": getattr(receipt, "error", "") or "",
            })
        except Exception as inner:
            return _err(f"send failed: {inner}", status=502)
    except Exception as exc:
        logger.exception("messages send failed")
        return _err(str(exc))
