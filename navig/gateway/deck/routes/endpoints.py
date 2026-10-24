"""Read-only Unified Endpoints handlers for the Deck API.

The deck shows what NAVIG can control; it never changes it — mutations go through
``navig endpoints set`` (and, later, the NAVIG Reflex's plan/commit). Both routes
read the shipped home plugin through :class:`~navig.endpoints.store.EndpointStore`
(no YAML parsing here, no second inventory):

* ``GET /api/deck/endpoints`` — the projection: ``{ok, count, endpoints[],
  providers[], unmapped}``. Optional ``?provider=`` and ``?room=`` filters are
  whitespace-stripped and bounded before they reach the store.
* ``GET /api/deck/endpoints/{endpoint_id}`` — one endpoint, ``404`` when the id is
  unknown, ``503`` with the plugin's actionable hint when the home plugin is
  missing or not ready.

The store's reads spawn child processes, so every call runs in a worker thread —
a slow provider must never freeze the daemon's event loop (the monitor routes
follow the same rule). No auth is added here: the deck's middleware enforces it
globally for every route.
"""

from __future__ import annotations

import asyncio
import logging

try:
    from aiohttp import web
except ImportError:  # pragma: no cover — the deck only loads with aiohttp present
    web = None

from navig.endpoints.errors import EndpointError, EndpointNotFound, HomeUnavailable
from navig.endpoints.store import EndpointStore

logger = logging.getLogger(__name__)

#: A provider/room id is a short slug; anything longer is a mistake, not a filter.
_MAX_FILTER_CHARS = 64


class _FilterError(ValueError):
    """A query filter that cannot be a provider/room name."""


def _store() -> EndpointStore:
    """Build the production store. Tests patch this to inject a fake bridge."""
    return EndpointStore()


def _error(message: str, *, status: int, hint: str = "") -> "web.Response":
    payload: dict[str, object] = {"ok": False, "error": str(message)}
    if hint:
        payload["hint"] = str(hint)
    return web.json_response(payload, status=status)


def _filter(request: "web.Request", name: str) -> str:
    """Whitespace-stripped, length-bounded query filter ("" means no filter)."""
    raw = str(request.query.get(name) or "").strip()
    if len(raw) > _MAX_FILTER_CHARS:
        raise _FilterError(f"{name} filter is too long (max {_MAX_FILTER_CHARS} chars)")
    if any(ord(character) < 32 for character in raw):
        raise _FilterError(f"{name} filter contains control characters")
    return raw


async def handle_deck_endpoints_list(request: "web.Request") -> "web.Response":
    """Every controllable endpoint, optionally filtered by provider or room."""
    try:
        provider = _filter(request, "provider")
        room = _filter(request, "room")
    except _FilterError as exc:
        return _error(str(exc), status=400)
    store = _store()
    try:
        endpoints = await asyncio.to_thread(
            lambda: store.list(provider=provider or None, room=room or None))
        providers = await asyncio.to_thread(store.providers)
        unmapped = await asyncio.to_thread(store.unmapped)
    except HomeUnavailable as exc:
        return _error(str(exc), status=503, hint=exc.hint)
    except EndpointError as exc:
        return _error(str(exc), status=500, hint=exc.hint)
    return web.json_response({
        "ok": True,
        "count": len(endpoints),
        "endpoints": [endpoint.as_dict() for endpoint in endpoints],
        "providers": providers,
        "unmapped": unmapped,
    })


async def handle_deck_endpoints_show(request: "web.Request") -> "web.Response":
    """One endpoint by ``<provider>:<device id>`` — read-only."""
    endpoint_id = str(request.match_info.get("endpoint_id") or "").strip()
    if not endpoint_id:
        return _error("endpoint id is required", status=400)
    store = _store()
    try:
        endpoint = await asyncio.to_thread(store.show, endpoint_id)
    except EndpointNotFound as exc:
        return _error(str(exc), status=404, hint=exc.hint)
    except HomeUnavailable as exc:
        return _error(str(exc), status=503, hint=exc.hint)
    except EndpointError as exc:
        return _error(str(exc), status=500, hint=exc.hint)
    return web.json_response({"ok": True, "endpoint": endpoint.as_dict()})
