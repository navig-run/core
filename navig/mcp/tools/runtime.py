from typing import Any, Dict


def register(server: Any) -> None:
    """Register runtime monitoring and control tools."""
    server.tools.update(
        {
            "navig_runtime_list_nodes": {
                "name": "navig_runtime_list_nodes",
                "description": "List registered NAVIG Nodes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": "Filter by status: online, offline, etc.",
                        }
                    },
                    "required": [],
                },
            },
            "navig_runtime_create_mission": {
                "name": "navig_runtime_create_mission",
                "description": "Create a new Mission.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "node_id": {"type": "string"},
                        "capability": {"type": "string"},
                        "payload": {"type": "object"},
                        "priority": {"type": "integer"},
                    },
                    "required": ["title"],
                },
            },
            "navig_runtime_mission_action": {
                "name": "navig_runtime_mission_action",
                "description": "Advance a Mission state. Actions: start, succeed, fail:<msg>, cancel:<reason>, timeout.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mission_id": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["mission_id", "action"],
                },
            },
            "navig_runtime_list_missions": {
                "name": "navig_runtime_list_missions",
                "description": "List Missions, optionally filtered by node or status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "status": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": [],
                },
            },
            "navig_runtime_list_receipts": {
                "name": "navig_runtime_list_receipts",
                "description": "List ExecutionReceipts, optionally filtered by node or mission.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "mission_id": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": [],
                },
            },
            "navig_runtime_trust_score": {
                "name": "navig_runtime_trust_score",
                "description": "Compute TrustScore for a Node from its receipt history.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"node_id": {"type": "string"}},
                    "required": ["node_id"],
                },
            },
        }
    )

    server._tool_handlers.update(
        {
            "navig_runtime_list_nodes": _tool_runtime_list_nodes,
            "navig_runtime_create_mission": _tool_runtime_create_mission,
            "navig_runtime_mission_action": _tool_runtime_mission_action,
            "navig_runtime_list_missions": _tool_runtime_list_missions,
            "navig_runtime_list_receipts": _tool_runtime_list_receipts,
            "navig_runtime_trust_score": _tool_runtime_trust_score,
        }
    )


def _tool_runtime_list_nodes(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """List registered Nodes."""
    from navig.contracts.node import NodeStatus
    from navig.contracts.store import get_runtime_store

    store = get_runtime_store()
    status_filter = args.get("status")
    status = NodeStatus(status_filter) if status_filter else None
    nodes = store.list_nodes(status=status)
    return {"nodes": [n.to_dict() for n in nodes], "count": len(nodes)}


def _tool_runtime_create_mission(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new Mission."""
    from navig.contracts.mission import Mission
    from navig.contracts.store import get_runtime_store

    if not args.get("title"):
        return {"error": "title is required"}
    store = get_runtime_store()
    m = Mission(
        title=args["title"],
        node_id=args.get("node_id"),
        capability=args.get("capability", ""),
        payload=args.get("payload", {}),
        priority=int(args.get("priority", 50)),
    )
    store.create_mission(m)
    store.flush()
    return {"mission": m.to_dict(), "ok": True}


def _tool_runtime_mission_action(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Advance a Mission lifecycle."""
    from navig.contracts.store import get_runtime_store

    store = get_runtime_store()
    mission_id = args.get("mission_id", "")
    action = args.get("action", "")
    if not mission_id or not action:
        return {"error": "mission_id and action are required"}
    try:
        m = store.advance_mission(mission_id, action)
        store.flush()
        return {"mission": m.to_dict(), "ok": True}
    except (KeyError, ValueError) as e:
        return {"error": str(e)}


def _tool_runtime_list_missions(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """List Missions."""
    from navig.contracts.mission import MissionStatus
    from navig.contracts.store import get_runtime_store

    store = get_runtime_store()
    status_str = args.get("status")
    status = MissionStatus(status_str) if status_str else None
    missions = store.list_missions(
        node_id=args.get("node_id"),
        status=status,
        limit=int(args.get("limit", 20)),
    )
    return {"missions": [m.to_dict() for m in missions], "count": len(missions)}


def _tool_runtime_list_receipts(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """List ExecutionReceipts."""
    from navig.contracts.store import get_runtime_store

    store = get_runtime_store()
    receipts = store.list_receipts(
        node_id=args.get("node_id"),
        mission_id=args.get("mission_id"),
        limit=int(args.get("limit", 20)),
    )
    return {"receipts": [r.to_dict() for r in receipts], "count": len(receipts)}


def _tool_runtime_trust_score(server: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    """Compute TrustScore for a Node."""
    from navig.contracts.store import get_runtime_store

    node_id = args.get("node_id", "")
    if not node_id:
        return {"error": "node_id is required"}
    store = get_runtime_store()
    ts = store.compute_trust_score(node_id)
    return ts.to_dict()
