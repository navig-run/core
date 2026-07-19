"""Mesh aliases for the Deck API (slice B6).

The canonical Flux-mesh HTTP surface lives at the gateway ROOT
(``navig/gateway/routes/mesh.py`` — /mesh/peers, /mesh/ping, /mesh/route…).
The desktop OS renderer, however, can only reach ``/api/deck/*``: its brain
proxy (apps/os server-core ``deckFetch``) hard-prefixes every path with
``/api/deck``. These aliases close that transport gap over the exact same
``navig.mesh`` objects the root routes serve — Phase-1 LAN-only rules
intact: UDP multicast discovery (224.0.0.251:5354), no WAN, routing
mutation verbs (ping/route/target) stay at the gateway root.

The scan alias mirrors the (repaired) root ``POST /mesh/discovery/scan``:
it nudges the LIVE ``MeshDiscovery`` held by the gateway
(``gateway._mesh_discovery`` — the one instance ``server.py`` starts) via
``announce()`` — one extra HELLO multicast; peers upsert us and reply with
their own HELLO, converging the registry within ~1 heartbeat interval.
Mesh disabled / loop not running → ``scanning: false`` (200, graceful).

Routes (registered in navig/gateway/deck/__init__.py):
    GET  /api/deck/mesh/peers → {self, peers} — NodeRegistry.to_api_dict()
    POST /api/deck/mesh/scan  → announce() nudge + fresh {self, peers} snapshot
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def handle_mesh_peers(request: "web.Request") -> "web.Response":
    """Self + all known LAN peers (health computed from heartbeat age)."""
    try:
        from navig.mesh.registry import get_registry

        gateway = request.app.get("gateway")
        registry = get_registry(getattr(gateway, "storage_dir", None))
        return _ok(registry.to_api_dict())
    except Exception as exc:
        logger.exception("mesh peers read failed")
        return _err(str(exc), 500)


async def handle_mesh_scan(request: "web.Request") -> "web.Response":
    """On-demand discovery nudge: announce() over the LIVE MeshDiscovery.

    Graceful by design (Phase-1 mesh rule): when mesh is disabled or the
    discovery loop is not running, responds 200 with ``scanning: false`` —
    the OS refresh flow falls back to a plain registry re-read.
    """
    try:
        gateway = request.app.get("gateway")
        discovery = getattr(gateway, "_mesh_discovery", None)
        announced = False
        if discovery is not None:
            try:
                announced = bool(await discovery.announce())
            except Exception:  # noqa: BLE001 — mesh ops never hard-fail
                logger.exception("mesh scan announce failed")
        if not announced:
            return _ok({"scanning": False, "reason": "mesh not running"})

        from navig.mesh.registry import get_registry

        snapshot = get_registry(getattr(gateway, "storage_dir", None)).to_api_dict()
        return _ok(
            {
                "scanning": True,
                "self": snapshot.get("self"),
                "peers": snapshot.get("peers", []),
            }
        )
    except Exception as exc:
        logger.exception("mesh scan failed")
        return _err(str(exc), 500)
