"""
Deck module-registry endpoints.

GET  /api/deck/modules            → the canonical module catalog (id/label/kind/
   category/icon/surfaces/tier-lock/enabled). navig-os + the deck render tiles
   from THIS instead of a hardcoded array, so the CLI, deck, and os never drift.
POST /api/deck/modules/toggle     → {"id": "...", "enabled": true} persists a
   per-operator enable/disable override.

Tier locks are resolved server-side against the Harbor entitlement
(`license.current_status`); the client only reflects `locked`.
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


async def emit_modules_update(request: "web.Request", payload: dict[str, Any]) -> None:
    """Broadcast `modules_update` on /api/events (best-effort, never raises).

    Fired after any catalog mutation (toggle here; installs in catalog.py /
    store.py import this) so live surfaces refresh without polling. The OS app
    additionally re-hydrates deterministically (boot/focus/space-switch).
    """
    try:
        gw = request.app.get("gateway")
        queue = getattr(gw, "system_events", None) if gw else None
        if queue and hasattr(queue, "emit"):
            await queue.emit("modules_update", payload)
    except Exception:  # noqa: BLE001
        logger.debug("modules_update emit failed", exc_info=True)


async def notify_modules_changed(request: "web.Request", payload: dict[str, Any]) -> None:
    """The post-install/removal refresh contract, in ONE place.

    After a catalog mutation that added or removed a plugin (Bay install, store
    drag-drop, store enable/disable/remove): (1) re-arm entry-point discovery so a
    freshly pip-installed plugin's module appears on the NEXT catalog GET — no
    daemon restart — then (2) broadcast `modules_update` so live surfaces
    re-hydrate. Every daemon install door should call this instead of hand-rolling
    the pair (that divergence is exactly how `/bay/acquire` shipped missing it).

    The one deliberate exception is `acquire_bay_item`: it is SYNC and also backs
    the `navig_bay_acquire` MCP tool (no request/SSE), so it calls
    `reset_entry_points()` in its sync core and emits from its async wrapper.
    Both steps here are best-effort — a refresh must never fail the mutation.
    """
    try:
        from navig.modules.registry import reset_entry_points  # noqa: PLC0415

        reset_entry_points()
    except Exception:  # noqa: BLE001
        pass
    await emit_modules_update(request, payload)


async def handle_deck_modules(request: "web.Request") -> "web.Response":
    """Return the resolved module catalog with tier-lock + enabled flags."""
    try:
        from navig.modules.registry import (
            CATEGORY_LABELS,
            CATEGORY_ORDER,
            get_registry,
        )

        include_dev = request.query.get("dev") in ("1", "true", "yes")
        modules = get_registry().discover().list_modules(include_dev=include_dev)
        return web.json_response({
            "ok": True,
            "modules": modules,
            "category_order": CATEGORY_ORDER,
            "category_labels": CATEGORY_LABELS,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck modules endpoint failed")
        return _err(str(exc))


async def handle_deck_modules_toggle(request: "web.Request") -> "web.Response":
    """Persist a per-operator enable/disable override for one module."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON body", status=400)

    module_id = str(body.get("id") or "").strip()
    if not module_id:
        return _err("missing 'id'", status=400)
    enabled = bool(body.get("enabled", True))

    try:
        from navig.modules.registry import get_registry

        reg = get_registry().discover()
        if not reg.set_enabled(module_id, enabled):
            return _err(f"unknown module '{module_id}'", status=404)
        await emit_modules_update(request, {"kind": "toggle", "id": module_id, "enabled": enabled})
        return web.json_response({"ok": True, "id": module_id, "enabled": enabled})
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck modules toggle failed")
        return _err(str(exc))
