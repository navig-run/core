"""
Mesh gateway routes.

GET  /mesh/peers          → returns NodeRegistry.to_api_dict() (self + all known peers)
POST /mesh/ping           → add/refresh a peer manually (URL in body), for NAT-traversal
POST /mesh/route          → proxy a ChatRequest to the best available peer
POST /mesh/discovery/scan → on-demand discovery nudge: HELLO multicast over the LIVE
                            MeshDiscovery + a registry snapshot ({scanning:false} when
                            mesh is disabled — graceful, never a hard failure)

A2A (Agent2Agent) discovery — expose this node as a standard agent:
GET  /.well-known/agent-card.json → this node's A2A Agent Card (canonical path)
GET  /.well-known/agent.json      → legacy A2A alias (older drafts)
GET  /mesh/agents                 → every mesh peer rendered as an A2A Agent Card

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
    raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from _exc

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
    # A2A discovery — the well-known Agent Card + a mesh→A2A bridge listing.
    app.router.add_get("/.well-known/agent-card.json", _agent_card(gateway))
    app.router.add_get("/.well-known/agent.json", _agent_card(gateway))  # legacy alias
    app.router.add_get("/mesh/agents", _agents(gateway))
    # A2A messaging — JSON-RPC 2.0 endpoint the Agent Card advertises as its url.
    app.router.add_post("/a2a", _a2a_rpc(gateway))


# ────────────────────── GET /.well-known/agent-card.json ─────────────


def _agent_card(gw: NavigGateway):
    """Serve this node's A2A Agent Card (public discovery — no auth).

    The card is the standard way an A2A client learns what this node can do;
    it advertises only the same low-sensitivity metadata already gossiped over
    the LAN mesh (hostname, OS, capabilities), never secrets.
    """

    async def h(r: web.Request) -> web.Response:
        from navig.mesh.agent_card import build_agent_card

        registry = get_registry(gw.storage_dir)
        card = build_agent_card(registry.self_record)
        return web.json_response(card)

    return h


# ─────────────────────────── GET /mesh/agents ────────────────────────


def _agents(gw: NavigGateway):
    """Render every known mesh node (self + peers) as an A2A Agent Card.

    This is the bridge that turns LAN mesh discovery into A2A discovery: one
    fetch yields a standard Agent Card for each reachable NAVIG node.
    """

    async def h(r: web.Request) -> web.Response:
        from navig.mesh.agent_card import build_agent_card

        registry = get_registry(gw.storage_dir)
        cards = [build_agent_card(registry.self_record)]
        cards.extend(build_agent_card(peer) for peer in registry.get_peers())
        return json_ok({"agents": cards})

    return h


# ─────────────────────────── POST /a2a ───────────────────────────────


def _a2a_rpc(gw: NavigGateway):
    """A2A JSON-RPC 2.0 endpoint — run a message against the local agent.

    Discovery (the Agent Card) is public, but *acting* on this node runs the
    NAVIG agent (which can touch real infrastructure), so this endpoint is
    authenticated with the same bearer guard as ``/mesh/route``. v0 supports
    the ``message/send`` method and replies with an A2A Message.
    """

    async def h(r: web.Request) -> web.Response:
        from navig.mesh import a2a

        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        try:
            body = await r.json()
        except Exception:
            return web.json_response(
                a2a.rpc_error(None, a2a.PARSE_ERROR, "Invalid JSON")
            )

        ok, reason = a2a.validate_request(body)
        if not ok:
            return web.json_response(
                a2a.rpc_error(body.get("id") if isinstance(body, dict) else None,
                              a2a.INVALID_REQUEST, reason)
            )

        req_id = body.get("id")
        method = body["method"]
        if method != a2a.METHOD_MESSAGE_SEND:
            return web.json_response(
                a2a.rpc_error(req_id, a2a.METHOD_NOT_FOUND, f"Unknown method: {method}")
            )

        params = body.get("params") or {}
        message = params.get("message")
        if not isinstance(message, dict):
            return web.json_response(
                a2a.rpc_error(req_id, a2a.INVALID_PARAMS, "params.message is required")
            )

        text = a2a.extract_text(message)
        if not text:
            return web.json_response(
                a2a.rpc_error(req_id, a2a.INVALID_PARAMS, "message has no text part")
            )

        try:
            metadata = {"scope": "personal", "flags": {}, "source": "a2a"}
            reply = await gw.router.route_message(
                channel="a2a", user_id="a2a", message=text, metadata=metadata
            )
            return web.json_response(
                a2a.rpc_result(req_id, a2a.build_message(reply or ""))
            )
        except Exception as e:
            logger.warning("[a2a] message/send failed: %s", e)
            return web.json_response(
                a2a.rpc_error(req_id, a2a.INTERNAL_ERROR, f"agent error: {e}")
            )

    return h


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
            return json_error_response("Invalid JSON body", status=400, code="bad_request")

        url = (body.get("gateway_url") or "").rstrip("/")
        if not url:
            return json_error_response("gateway_url required", status=400, code="bad_request")

        result = await _bootstrap_peer(url, gw)
        if result:
            return json_ok({"bootstrapped": True, "peer": result})
        return json_error_response(
            "Could not reach peer at " + url, status=502, code="peer_unreachable"
        )

    return h


def _is_safe_mesh_url(url: str) -> bool:
    """Mesh is LAN-only by design — block SSRF to public hosts / cloud metadata.

    Requires http(s) and that the host (resolving hostnames) maps only to private
    or loopback addresses. Rejects public IPs and link-local (169.254.169.254
    metadata is link-local, which Python counts as private, so exclude it explicitly).
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    def _ip_ok(ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return (ip.is_private or ip.is_loopback) and not ip.is_link_local

    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname
    try:
        ipaddress.ip_address(host)
        return _ip_ok(host)  # IP literal
    except ValueError:
        pass
    # Hostname → resolve; EVERY resolved address must be LAN/loopback (blocks
    # public domains and cloud-metadata names). Unresolvable → reject (can't verify).
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(_ip_ok(a) for a in addrs)


async def _bootstrap_peer(url: str, gw: NavigGateway) -> dict | None:
    """Fetch /health then /mesh/peers from a manually-specified peer URL."""
    if not _is_safe_mesh_url(url):
        logger.warning("[mesh.routes] Rejected non-LAN bootstrap URL (SSRF guard): %s", url)
        return None
    try:
        import time

        import aiohttp

        from navig.mesh.registry import NodeRecord, get_registry

        async with aiohttp.ClientSession() as session:
            # Quick health check
            async with session.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
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
        logger.warning("[mesh.routes] Bootstrap failed for %s: %s", url, e)
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
            return json_error_response("Invalid JSON body", status=400, code="bad_request")

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
            return json_error_response(
                f"Local fallback failed: {e}", status=500, code="internal_error"
            )

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
            return json_error_response("Invalid JSON body", status=400, code="bad_request")

        node_id = (body.get("node_id") or "").strip()
        if not node_id:
            return json_error_response("node_id required", status=400, code="bad_request")

        registry = get_registry(gw.storage_dir)
        match = registry.get_peer(node_id)  # type: ignore[attr-defined]
        if match is None:
            # Try prefix match
            all_peers = registry.list_peers()  # type: ignore[attr-defined]
            match = next((p for p in all_peers if p.node_id.startswith(node_id)), None)
        if match is None:
            return json_error_response(f"Peer '{node_id}' not found", status=404, code="not_found")

        # Mark as current target in registry
        registry.set_target(match.node_id)  # type: ignore[attr-defined]
        logger.info("[mesh.routes] Target set to %s", match.node_id)
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
    Trigger an immediate LAN discovery nudge over the LIVE MeshDiscovery.

    The gateway holds exactly one running ``MeshDiscovery`` instance
    (``server.py:_init_autonomous_modules`` → ``gw._mesh_discovery``); its
    ``announce()`` multicasts one extra HELLO — peers upsert us and reply
    with their own HELLO, so the registry converges within ~1 heartbeat
    interval. No new packet types, no WAN, no second discovery instance.

    Degrades gracefully (Phase-1 rule): mesh disabled / loop not running →
    200 with ``scanning: false`` — never a hard failure.
    """

    async def h(r: web.Request) -> web.Response:
        discovery = getattr(gw, "_mesh_discovery", None)
        announced = False
        if discovery is not None:
            try:
                announced = bool(await discovery.announce())
            except Exception as e:  # noqa: BLE001 — mesh ops never hard-fail
                logger.warning("[mesh.routes] Scan announce failed: %s", e)
        if not announced:
            return json_ok({"scanning": False, "reason": "mesh not running"})
        snapshot = get_registry(gw.storage_dir).to_api_dict()
        return json_ok(
            {
                "scanning": True,
                "self": snapshot.get("self"),
                "peers": snapshot.get("peers", []),
                "hint": "poll /mesh/peers in ~2s for late repliers",
            }
        )

    return h
