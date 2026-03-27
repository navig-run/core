"""
Mesh gateway routes.

GET  /mesh/peers       → returns NodeRegistry.to_api_dict() (self + all known peers)
POST /mesh/ping        → add/refresh a peer manually (URL in body), for NAT-traversal
POST /mesh/route       → proxy a ChatRequest to the best available peer

These routes extend the existing API — nothing existing is changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway

try:
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError(
        "aiohttp is required for gateway routes (pip install aiohttp)"
    ) from _exc

from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    json_error_response,
    json_ok,
    require_bearer_auth,
)
from navig.mesh.registry import get_registry

logger = get_debug_logger()


def register(app: web.Application, gateway: NavigGateway) -> None:
    app.router.add_get("/mesh/peers", _peers(gateway))
    app.router.add_post("/mesh/ping", _ping(gateway))
    app.router.add_post("/mesh/route", _route(gateway))
    app.router.add_post("/mesh/target", _set_target(gateway))
    app.router.add_delete("/mesh/target", _clear_target(gateway))
    app.router.add_post("/mesh/discovery/scan", _scan(gateway))


# ─────────────────────────── GET /mesh/peers ─────────────────────────


def _peers(gw: NavigGateway):
    async def h(r: web.Request) -> web.Response:
        # Light auth: optional — mesh data is low-sensitivity on LAN
        registry = get_registry(gw.storage_dir)
        return json_ok(registry.to_api_dict())

    return h


# ─────────────────────────── POST /mesh/ping ─────────────────────────


def _ping(gw: NavigGateway):
    """
    Manually register a peer by its gateway URL.
    Body: { "gateway_url": "http://10.0.0.x:8789" }

    Useful when UDP multicast is blocked (e.g., some corporate networks).
    The server fetches /health + /mesh/peers from the given URL to bootstrap.
    """

    async def h(r: web.Request) -> web.Response:
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        try:
            body = await r.json()
        except Exception:
            return json_error_response("Invalid JSON body", status=400)

        url = (body.get("gateway_url") or "").rstrip("/")
        if not url:
            return json_error_response("gateway_url required", status=400)

        result = await _bootstrap_peer(url, gw)
        if result:
            return json_ok({"bootstrapped": True, "peer": result})
        return json_error_response("Could not reach peer at " + url, status=502)

    return h


async def _bootstrap_peer(url: str, gw: NavigGateway) -> dict | None:
    """Fetch /health then /mesh/peers from a manually-specified peer URL."""
    try:
        import time

        import aiohttp

        from navig.mesh.registry import NodeRecord, get_registry

        async with aiohttp.ClientSession() as session:
            # Quick health check
            async with session.get(
                f"{url}/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    return None

            # Fetch their peer data to get their self record
            async with session.get(
                f"{url}/mesh/peers", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        peer_data = data.get("data", {}).get("self") or data.get("self")
        if not peer_data:
            return None

        record = NodeRecord.from_dict(peer_data)
        record.last_seen = time.time()
        registry = get_registry(gw.storage_dir)
        registry.upsert_peer(record)
        return record.to_dict()

    except Exception as e:
        logger.warning(f"[mesh.routes] Bootstrap failed for {url}: {e}")
        return None


# ─────────────────────────── POST /mesh/route ────────────────────────


def _route(gw: NavigGateway):
    """
    Proxy a ChatRequest to the best available mesh peer.
    Body: ChatRequest JSON + optional "target_node_id" and "capability" fields.

    Returns the peer's /llm/chat response with added `routed_via` metadata.
    Falls back to local processing if no peer is available.
    """

    async def h(r: web.Request) -> web.Response:
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        try:
            body = await r.json()
        except Exception:
            return json_error_response("Invalid JSON body", status=400)

        target_node_id = body.pop("target_node_id", None)
        capability = body.pop("capability", None)

        from navig.mesh.router import route_to_best_peer

        result = await route_to_best_peer(
            body, capability=capability, target_node_id=target_node_id
        )

        if result is not None:
            return json_ok(result)

        # No peer available — route locally via the gateway's own pipeline
        logger.info("[mesh.routes] No peer available, processing locally")
        try:
            text = body.get("text", "")
            scope = body.get("scope", "personal")
            flags = body.get("flags", {})
            metadata = {"scope": scope, "flags": flags, "source": "mesh_fallback"}
            response_text = await gw.router.route_message(
                channel="http", user_id="bridge", message=text, metadata=metadata
            )
            return json_ok(
                {
                    "text": response_text or "",
                    "routed_via": None,
                    "metadata": {"provider": "local"},
                }
            )
        except Exception as e:
            return json_error_response(f"Local fallback failed: {e}", status=500)

    return h


# ─────────────────────────── POST /mesh/target ───────────────────────


def _set_target(gw: NavigGateway):
    """
    Set the active routing target for this session.
    Body: { "node_id": "<node_id>" }
    The target is stored in-memory on the gateway (not persisted).
    """

    async def h(r: web.Request) -> web.Response:
        try:
            body = await r.json()
        except Exception:
            return json_error_response("Invalid JSON body", status=400)

        node_id = (body.get("node_id") or "").strip()
        if not node_id:
            return json_error_response("node_id required", status=400)

        registry = get_registry(gw.storage_dir)
        match = registry.get_peer(node_id)  # type: ignore[attr-defined]
        if match is None:
            # Try prefix match
            all_peers = registry.list_peers()  # type: ignore[attr-defined]
            match = next((p for p in all_peers if p.node_id.startswith(node_id)), None)
        if match is None:
            return json_error_response(f"Peer '{node_id}' not found", status=404)

        # Mark as current target in registry
        registry.set_target(match.node_id)  # type: ignore[attr-defined]
        logger.info(f"[mesh.routes] Target set to {match.node_id}")
        return json_ok({"target": match.to_dict()})

    return h


# ─────────────────────────── DELETE /mesh/target ─────────────────────


def _clear_target(gw: NavigGateway):
    """Clear the active routing target — commands run locally."""

    async def h(r: web.Request) -> web.Response:
        registry = get_registry(gw.storage_dir)
        registry.clear_target()  # type: ignore[attr-defined]
        logger.info("[mesh.routes] Routing target cleared")
        return json_ok({"cleared": True})

    return h


# ─────────────────────────── POST /mesh/discovery/scan ───────────────


def _scan(gw: NavigGateway):
    """
    Trigger an immediate LAN discovery scan (UDP multicast + active probing).
    Non-blocking — returns immediately; listen on /mesh/peers for results.
    """

    async def h(r: web.Request) -> web.Response:
        import asyncio

        try:
            from navig.mesh.discovery import NavigDiscovery

            registry = get_registry(gw.storage_dir)
            discovery = NavigDiscovery(registry)

            async def _do_scan() -> None:
                try:
                    await discovery.probe_lan_range()
                except Exception as e:
                    logger.warning(f"[mesh.routes] Scan error: {e}")

            asyncio.create_task(_do_scan())
            return json_ok({"scanning": True, "hint": "poll /mesh/peers in ~2s"})
        except Exception as e:
            logger.warning(f"[mesh.routes] Could not start scan: {e}")
            return json_ok({"scanning": False, "error": str(e)})

    return h
