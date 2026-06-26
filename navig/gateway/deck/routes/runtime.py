"""Runtime (Nodes / Missions / Receipts) endpoints for the Deck API.

Backed by navig.contracts.store.RuntimeStore — the durable in-memory store
that tracks Node registrations, Missions, and ExecutionReceipts.
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

# ── Status adapters ────────────────────────────────────────────────────────
# RuntimeStore uses its own enums; the Deck API uses the frontend-friendly
# string literals defined in lib/types.ts.

_NODE_STATE_MAP = {
    "online":          "active",
    "offline":         "idle",
    "provisioning":    "idle",
    "suspended":       "suspended",
    "decommissioned":  "terminated",
}

_MISSION_STATE_MAP = {
    "queued":    "queued",
    "running":   "active",
    "succeeded": "completed",
    "failed":    "failed",
    "cancelled": "cancelled",
    "timed_out": "failed",
}


def _get_store():
    """Return the shared RuntimeStore singleton (best-effort; None on failure).

    MUST be the singleton (not a fresh ``RuntimeStore()``) so missions the
    MissionExecutor is driving in-process show their live status here instead of
    a stale on-disk snapshot.
    """
    try:
        from navig.contracts.store import get_runtime_store  # type: ignore[import]
        return get_runtime_store()
    except Exception as exc:
        logger.debug("RuntimeStore unavailable: %s", exc)
        return None


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_runtime_nodes(request: "web.Request") -> "web.Response":
    """GET /runtime/nodes — list all registered nodes."""
    status_filter = request.rel_url.query.get("status")
    store = _get_store()
    nodes: list[dict] = []

    if store:
        try:
            from navig.contracts.node import NodeStatus  # type: ignore[import]
            sf = NodeStatus(status_filter) if status_filter else None
            raw_nodes = store.list_nodes(status=sf)
        except Exception:
            raw_nodes = store.list_nodes()

        for n in raw_nodes:
            # n is a Node dataclass; use to_dict() if available, else asdict()
            try:
                nd = n.to_dict() if hasattr(n, "to_dict") else vars(n)
            except Exception:
                nd = {}

            raw_state = str(nd.get("status", "offline")).lower()
            nodes.append({
                "id":           nd.get("node_id", ""),
                "name":         nd.get("hostname", nd.get("node_id", "unknown")),
                "state":        _NODE_STATE_MAP.get(raw_state, "idle"),
                "trust_score":  float(nd.get("trust_score", 0.0)),
                "capabilities": list(nd.get("capabilities", [])),
                "ip":           nd.get("ip") or nd.get("host"),
                "version":      nd.get("version"),
                "last_seen":    nd.get("last_seen"),
            })

    return web.json_response({"nodes": nodes, "total": len(nodes)})


async def handle_runtime_missions(request: "web.Request") -> "web.Response":
    """GET /runtime/missions — list missions (newest first)."""
    node_id = request.rel_url.query.get("node_id")
    limit = min(int(request.rel_url.query.get("limit", "100")), 500)
    store = _get_store()
    missions: list[dict] = []

    if store:
        try:
            raw_missions = store.list_missions(node_id=node_id, limit=limit)
        except Exception:
            raw_missions = []

        for m in raw_missions:
            try:
                md = m.to_dict() if hasattr(m, "to_dict") else vars(m)
            except Exception:
                md = {}

            raw_state = str(md.get("status", "queued")).lower()
            missions.append({
                "id":         md.get("mission_id", ""),
                "title":      md.get("title", md.get("capability", "Mission")),
                "state":      _MISSION_STATE_MAP.get(raw_state, raw_state),
                "agent_id":   md.get("node_id"),
                "created_at": md.get("created_at", ""),
                "updated_at": md.get("updated_at", md.get("created_at", "")),
            })

    return web.json_response({"missions": missions, "total": len(missions)})


async def handle_runtime_receipts(request: "web.Request") -> "web.Response":
    """GET /runtime/receipts — list execution receipts (newest first)."""
    node_id = request.rel_url.query.get("node_id")
    mission_id = request.rel_url.query.get("mission_id")
    limit = min(int(request.rel_url.query.get("limit", "50")), 500)
    store = _get_store()
    receipts: list[dict] = []

    if store:
        try:
            raw_receipts = store.list_receipts(
                node_id=node_id, mission_id=mission_id, limit=limit
            )
        except Exception:
            raw_receipts = []

        for r in raw_receipts:
            try:
                rd = r.to_dict() if hasattr(r, "to_dict") else vars(r)
            except Exception:
                rd = {}

            receipts.append({
                "receipt_id":    rd.get("receipt_id", ""),
                "mission_id":    rd.get("mission_id", ""),
                "node_id":       rd.get("node_id", ""),
                "title":         rd.get("title", rd.get("capability", "Task")),
                "capability":    rd.get("capability", ""),
                "outcome":       str(rd.get("outcome", "succeeded")).lower(),
                "completed_at":  rd.get("completed_at", rd.get("recorded_at", "")),
                "started_at":    rd.get("started_at"),
                "duration_secs": rd.get("duration_secs"),
                "error":         rd.get("error"),
                "recorded_at":   rd.get("recorded_at", ""),
            })

    return web.json_response({"receipts": receipts, "count": len(receipts)})


async def handle_runtime_mission_create(request: "web.Request") -> "web.Response":
    """POST /runtime/missions — enqueue a mission for the MissionExecutor.

    Body: {title, capability?, payload?, timeout_secs?, priority?, autonomy?}
    autonomy ∈ {draft, approval, auto}; omitted → operator's global level
    (default APPROVAL — never silent auto).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    title = (body.get("title") or "").strip()
    if not title:
        return web.json_response({"error": "title required"}, status=400)

    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    executor = getattr(gateway, "mission_executor", None)
    if executor is None:
        return web.json_response({"error": "mission executor unavailable"}, status=503)

    try:
        from navig.contracts.mission import Mission, MissionPriority

        metadata: dict = dict(body.get("metadata") or {})
        autonomy = body.get("autonomy")
        if isinstance(autonomy, str) and autonomy.lower() in ("draft", "approval", "auto"):
            metadata["autonomy"] = autonomy.lower()

        mission = Mission(
            title=title,
            capability=str(body.get("capability") or "agentic"),
            payload=dict(body.get("payload") or {}),
            priority=int(body.get("priority", MissionPriority.NORMAL.value)),
            timeout_secs=body.get("timeout_secs"),
            metadata=metadata,
        )
        await executor.submit(mission)
        return web.json_response({"id": mission.mission_id, "status": "queued"}, status=202)
    except Exception as exc:
        logger.warning("mission create failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=400)


async def handle_runtime_mission_advance(request: "web.Request") -> "web.Response":
    """POST /runtime/missions/{mission_id}/advance — advance mission state."""
    mission_id = request.match_info.get("mission_id", "")
    try:
        body = await request.json()
        new_state: str = body.get("new_state", "")
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    if not new_state:
        return web.json_response({"error": "new_state required"}, status=400)

    store = _get_store()
    if not store:
        return web.json_response({"error": "runtime store unavailable"}, status=503)

    # Map frontend state names → store action names
    _state_to_action = {
        "active":    "start",
        "completed": "succeed",
        "failed":    "fail",
        "cancelled": "cancel",
    }
    action = _state_to_action.get(new_state, new_state)

    try:
        mission = store.advance_mission(mission_id, action)
        md = mission.to_dict() if hasattr(mission, "to_dict") else vars(mission)
        raw_state = str(md.get("status", "queued")).lower()
        return web.json_response({
            "id":    mission_id,
            "state": _MISSION_STATE_MAP.get(raw_state, raw_state),
        })
    except KeyError:
        return web.json_response({"error": f"Mission {mission_id!r} not found"}, status=404)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=400)
