"""
Runtime contract gateway routes.

Node registry:
  GET  /runtime/nodes              → list all registered nodes
  POST /runtime/nodes              → register a new node
  GET  /runtime/nodes/{node_id}    → get single node

Mission lifecycle:
  GET  /runtime/missions           → list missions (query: node_id, status, limit)
  POST /runtime/missions           → create mission
  GET  /runtime/missions/{id}      → get single mission
  POST /runtime/missions/{id}/advance  → advance mission state machine (action in body)
  POST /runtime/missions/{id}/complete → mark mission done + create receipt

Receipt log:
  GET  /runtime/receipts           → list receipts (query: node_id, mission_id, limit)
  GET  /runtime/receipts/{id}      → get single receipt

Trust:
  GET  /runtime/nodes/{node_id}/trust  → compute trust score for node

Stats:
  GET  /runtime/store/stats        → RuntimeStore aggregate stats
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    json_error_response,
    json_ok,
    require_bearer_auth,
)

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway

try:
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError(
        "aiohttp is required for gateway routes (pip install aiohttp)"
    ) from _exc

logger = get_debug_logger()


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register all /runtime/* routes on the aiohttp Application."""
    # Node routes
    app.router.add_get("/runtime/nodes", _list_nodes(gateway))
    app.router.add_post("/runtime/nodes", _register_node(gateway))
    app.router.add_get("/runtime/nodes/{node_id}", _get_node(gateway))
    app.router.add_get("/runtime/nodes/{node_id}/trust", _get_trust(gateway))

    # Mission routes
    app.router.add_get("/runtime/missions", _list_missions(gateway))
    app.router.add_post("/runtime/missions", _create_mission(gateway))
    app.router.add_get("/runtime/missions/{mission_id}", _get_mission(gateway))
    app.router.add_post(
        "/runtime/missions/{mission_id}/advance", _advance_mission(gateway)
    )
    app.router.add_post(
        "/runtime/missions/{mission_id}/complete", _complete_mission(gateway)
    )

    # Receipt routes
    app.router.add_get("/runtime/receipts", _list_receipts(gateway))
    app.router.add_get("/runtime/receipts/{receipt_id}", _get_receipt(gateway))

    # Store stats
    app.router.add_get("/runtime/store/stats", _store_stats(gateway))


# ─────────────────────────── Node handlers ───────────────────────────


def _list_nodes(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        nodes = [n.to_dict() for n in store.list_nodes()]
        return json_ok({"nodes": nodes, "count": len(nodes)})

    return h


def _register_node(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            body = await r.json()
        except Exception:
            return json_error_response(
                "Invalid JSON body", status=400, code="bad_request"
            )

        actor = r.headers.get("X-Actor", r.remote or "unknown")
        block = await gw.policy_check("node.register", actor, raw_input=str(body))
        if block is not None:
            return block

        try:
            from navig.contracts.node import Node
            from navig.contracts.store import get_runtime_store

            node = Node.from_dict(body)
            store = get_runtime_store()
            registered = store.register_node(node)
            return json_ok(registered.to_dict(), status=201)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("register_node error: %s", exc)
            return json_error_response(str(exc), status=422, code="validation_error")
        except Exception as exc:
            logger.error("register_node unexpected: %s", exc)
            return json_error_response(
                "Internal error", status=500, code="internal_error"
            )

    return h


def _get_node(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        node_id = r.match_info["node_id"]
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        node = store.get_node(node_id)
        if node is None:
            return json_error_response(
                f"Node {node_id!r} not found", status=404, code="not_found"
            )
        return json_ok(node.to_dict())

    return h


def _get_trust(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        node_id = r.match_info["node_id"]
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        if store.get_node(node_id) is None:
            return json_error_response(
                f"Node {node_id!r} not found", status=404, code="not_found"
            )
        score = store.compute_trust_score(node_id)
        return json_ok(score.to_dict() if hasattr(score, "to_dict") else vars(score))

    return h


# ─────────────────────────── Mission handlers ────────────────────────


def _list_missions(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        node_id = r.rel_url.query.get("node_id")
        status = r.rel_url.query.get("status")
        try:
            limit = int(r.rel_url.query.get("limit", 100))
        except ValueError:
            limit = 100
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        missions = [
            m.to_dict()
            for m in store.list_missions(node_id=node_id, status=status, limit=limit)
        ]
        return json_ok({"missions": missions, "count": len(missions)})

    return h


def _create_mission(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            body = await r.json()
        except Exception:
            return json_error_response(
                "Invalid JSON body", status=400, code="bad_request"
            )

        actor = r.headers.get("X-Actor", r.remote or "unknown")
        block = await gw.policy_check("mission.create", actor, raw_input=str(body))
        if block is not None:
            return block

        try:
            from navig.contracts.mission import Mission
            from navig.contracts.store import get_runtime_store

            mission = Mission.from_dict(body)
            store = get_runtime_store()
            created = store.create_mission(mission)
            return json_ok(created.to_dict(), status=201)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("create_mission error: %s", exc)
            return json_error_response(str(exc), status=422, code="validation_error")
        except Exception as exc:
            logger.error("create_mission unexpected: %s", exc)
            return json_error_response(
                "Internal error", status=500, code="internal_error"
            )

    return h


def _get_mission(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        mission_id = r.match_info["mission_id"]
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        mission = store.get_mission(mission_id)
        if mission is None:
            return json_error_response(
                f"Mission {mission_id!r} not found", status=404, code="not_found"
            )
        return json_ok(mission.to_dict())

    return h


def _advance_mission(gw: "NavigGateway"):
    """
    POST /runtime/missions/{id}/advance
    Body: { "action": "start" | "succeed" | "fail" | "cancel" | "timeout" | "retry" }
    """

    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        mission_id = r.match_info["mission_id"]
        try:
            body = await r.json()
        except Exception:
            return json_error_response(
                "Invalid JSON body", status=400, code="bad_request"
            )

        action = str(body.get("action", "")).strip()
        if not action:
            return json_error_response(
                "'action' field required", status=422, code="validation_error"
            )

        actor = r.headers.get("X-Actor", r.remote or "unknown")
        block = await gw.policy_check(
            f"mission.advance.{action}", actor, raw_input=str(body)
        )
        if block is not None:
            return block

        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        try:
            updated = store.advance_mission(mission_id, action)
        except KeyError as exc:
            return json_error_response(str(exc), status=404, code="not_found")
        except ValueError as exc:
            return json_error_response(str(exc), status=422, code="invalid_transition")
        except Exception as exc:
            logger.error("advance_mission error: %s", exc)
            return json_error_response(
                "Internal error", status=500, code="internal_error"
            )

        return json_ok(updated.to_dict())

    return h


def _complete_mission(gw: "NavigGateway"):
    """
    POST /runtime/missions/{id}/complete
    Body: { "outcome": "success"|"failure"|"partial", "output": {...}, "error": "..." }
    """

    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        mission_id = r.match_info["mission_id"]
        try:
            body = await r.json()
        except Exception:
            return json_error_response(
                "Invalid JSON body", status=400, code="bad_request"
            )

        outcome = str(body.get("outcome", "success")).lower()
        output = body.get("output")
        error_msg = body.get("error")
        succeeded = outcome in ("success", "succeeded", "true", "1")

        actor = r.headers.get("X-Actor", r.remote or "unknown")
        block = await gw.policy_check("mission.complete", actor, raw_input=str(body))
        if block is not None:
            return block

        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        try:
            receipt = store.complete_mission(
                mission_id, succeeded=succeeded, result=output, error=error_msg
            )
        except KeyError as exc:
            return json_error_response(str(exc), status=404, code="not_found")
        except ValueError as exc:
            return json_error_response(str(exc), status=422, code="invalid_transition")
        except Exception as exc:
            logger.error("complete_mission error: %s", exc)
            return json_error_response(
                "Internal error", status=500, code="internal_error"
            )

        return json_ok(receipt.to_dict(), status=201)

    return h


# ─────────────────────────── Receipt handlers ────────────────────────


def _list_receipts(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        node_id = r.rel_url.query.get("node_id")
        mission_id = r.rel_url.query.get("mission_id")
        try:
            limit = int(r.rel_url.query.get("limit", 100))
        except ValueError:
            limit = 100
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        receipts = [
            rec.to_dict()
            for rec in store.list_receipts(
                node_id=node_id, mission_id=mission_id, limit=limit
            )
        ]
        return json_ok({"receipts": receipts, "count": len(receipts)})

    return h


def _get_receipt(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        receipt_id = r.match_info["receipt_id"]
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        receipt = store.get_receipt(receipt_id)
        if receipt is None:
            return json_error_response(
                f"Receipt {receipt_id!r} not found", status=404, code="not_found"
            )
        return json_ok(receipt.to_dict())

    return h


# ─────────────────────────── Stats handler ───────────────────────────


def _store_stats(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        from navig.contracts.store import get_runtime_store

        store = get_runtime_store()
        return json_ok(store.stats())

    return h
