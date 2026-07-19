"""
Deck Store endpoints — the hub surface over everything connectable.

GET  /api/deck/store          → {"items": [StoreItem…], "summary": store_status()}
   One list across modules (system plugins), installed plugins (all formats),
   skills, MCP servers, and connectors, each with a wire state
   (wired / unwired / available / broken) + badges (system/standalone/locked).
POST /api/deck/store/action   → {"id": "plugin:navig-social", "action": "disable"}
   Kind-dispatched enable/disable/install/remove (install accepts a `source`
   path — this is the drag-drop endpoint: the UI drops a folder/zip and posts
   its path here; validation runs through the plugin host).

`/api/deck/modules` (+ /toggle) stays untouched — os tiles keep consuming it;
this endpoint supersedes it for the Store page only. Recomputed per request
(adoption scan — no filesystem watcher), so a manually-dropped plugin folder
simply appears with its health badge.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def handle_deck_store(request: "web.Request") -> "web.Response":
    """Return every store item + the wiring summary."""
    try:
        from navig.hub import collect_store, store_status

        include_available = request.query.get("available", "1") not in ("0", "false")
        refresh = request.query.get("refresh") in ("1", "true", "yes")
        items = collect_store(include_available=include_available, refresh=refresh)
        return web.json_response({
            "ok": True,
            "items": [i.to_dict() for i in items],
            "summary": store_status(items),  # fold over the same collection — no second sweep
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck store endpoint failed")
        return _err(str(exc))


async def handle_deck_store_action(request: "web.Request") -> "web.Response":
    """Apply one action to one item (enable/disable/install/remove)."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON body", status=400)

    action = str(body.get("action") or "").strip()
    item_id = str(body.get("id") or "").strip()
    source = str(body.get("source") or "").strip()
    if not action:
        return _err("missing 'action'", status=400)
    if action == "install" and source:
        # Drag-drop: install from a dropped folder/zip path via the host.
        try:
            from navig.gateway.deck.routes.modules import notify_modules_changed
            from navig.plugins.host import get_plugin_host

            dest = get_plugin_host().install(source)
            await notify_modules_changed(request, {"kind": "install", "id": dest.name})
            return web.json_response({
                "ok": True,
                "message": f"Installed '{dest.name}'",
                "path": str(dest),
            })
        except ValueError as exc:
            return _err(str(exc), status=400)
        except Exception as exc:  # noqa: BLE001
            logger.exception("deck store install failed")
            return _err(str(exc))
    if not item_id:
        return _err("missing 'id'", status=400)

    try:
        from navig.gateway.deck.routes.modules import notify_modules_changed
        from navig.hub import apply_action

        result = apply_action(item_id, action)
        status = 200 if result.get("ok") else 400
        if result.get("ok") and action in ("install", "enable", "disable", "remove"):
            await notify_modules_changed(request, {"kind": action, "id": item_id})
        return web.json_response(result, status=status)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck store action failed")
        return _err(str(exc))
