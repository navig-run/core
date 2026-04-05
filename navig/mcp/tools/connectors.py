"""MCP Tool Bridge — Connector Auto-Discovery

Exposes every connector registered in ``ConnectorRegistry`` as MCP-callable
tools, zero duplication.

**Naming convention:**  ``connector.{id}.search`` / ``connector.{id}.fetch``
/ ``connector.{id}.act``  — one tool per enabled capability.

**Auto-discovery contract:**
- Adding a new connector to ``ConnectorRegistry`` automatically exposes it as
  MCP tools; no manual changes here are required.
- If a connector's ``manifest`` raises at startup → logged warning + skipped;
  the remaining tools load cleanly.
- If ``ConnectorRegistry.get(id)`` returns nothing at call-time →
  ``ConnectorNotFoundError`` bubbles to the MCP surface as-is.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.connectors.errors import ConnectorNotFoundError
from navig.connectors.registry import get_connector_registry
from navig.connectors.types import Action, ActionType

logger = logging.getLogger("navig.mcp.tools.connectors")


# ---------------------------------------------------------------------------
# Input-schema builders (per capability)
# ---------------------------------------------------------------------------


def _search_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query"},
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return",
                "default": 5,
            },
        },
        "required": ["query"],
    }


def _fetch_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "resource_id": {
                "type": "string",
                "description": "Native resource ID to fetch",
            },
        },
        "required": ["resource_id"],
    }


def _act_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action_type": {
                "type": "string",
                "description": (
                    "Action to perform. Allowed values: "
                    "reply, create, update, delete, archive, label, send, move"
                ),
            },
            "resource_id": {
                "type": "string",
                "description": "ID of the resource to act on (if applicable)",
            },
            "params": {
                "type": "object",
                "description": "Action-specific parameters (e.g. body, subject, labels)",
            },
        },
        "required": ["action_type"],
    }


# ---------------------------------------------------------------------------
# Tool enumeration
# ---------------------------------------------------------------------------


def list_connector_tools(registry=None) -> list[dict[str, Any]]:
    """Return one MCP tool-schema dict per connector capability.

    Iterates ``ConnectorRegistry.all_classes()`` so connectors need not be
    instantiated.  A connector whose ``manifest`` raises is logged as a
    warning and skipped; the remaining connectors are unaffected.

    Args:
        registry: A ``ConnectorRegistry`` instance.  Defaults to the global
            singleton (``get_connector_registry()``).

    Returns:
        List of MCP tool-schema dicts ready to be pushed into ``server.tools``.
    """
    reg = registry or get_connector_registry()
    tools: list[dict[str, Any]] = []

    for cid, cls in reg.all_classes().items():
        try:
            manifest = cls.manifest
            display = manifest.display_name
            desc = manifest.description
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Connector %r: manifest access failed (%s) — skipping MCP tool registration",
                cid,
                exc,
            )
            continue

        if manifest.can_search:
            tools.append(
                {
                    "name": f"connector.{cid}.search",
                    "description": f"Search {display}. {desc}",
                    "inputSchema": _search_input_schema(),
                }
            )

        if manifest.can_fetch:
            tools.append(
                {
                    "name": f"connector.{cid}.fetch",
                    "description": f"Fetch a single resource from {display} by its ID.",
                    "inputSchema": _fetch_input_schema(),
                }
            )

        if manifest.can_act:
            tools.append(
                {
                    "name": f"connector.{cid}.act",
                    "description": (
                        f"Execute a write action on {display} "
                        "(reply, create, update, delete, archive, label, send, move)."
                    ),
                    "inputSchema": _act_input_schema(),
                }
            )

    return tools


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def handle_connector_call(
    tool_name: str,
    params: dict[str, Any],
    registry=None,
) -> dict[str, Any]:
    """Route an MCP tool call to the matching registered connector.

    Expected *tool_name* format:  ``"connector.{id}.{operation}"``
    where *operation* is one of ``search``, ``fetch``, or ``act``.

    Args:
        tool_name: The MCP tool name as emitted by ``list_connector_tools``.
        params: Tool input parameters from the MCP caller.
        registry: Optional override; defaults to the global singleton.

    Returns:
        Serialisable dict result.

    Raises:
        ConnectorNotFoundError: Connector id is not registered.
        ValueError: Malformed tool name or unsupported operation.
    """
    parts = tool_name.split(".", 2)
    if len(parts) != 3 or parts[0] != "connector":
        raise ValueError(
            f"Invalid connector tool name {tool_name!r}. "
            "Expected format: 'connector.<id>.<operation>'"
        )
    _, connector_id, operation = parts

    reg = registry or get_connector_registry()
    # Raises ConnectorNotFoundError if not registered — intentional.
    connector = reg.get(connector_id)

    if operation == "search":
        query = params.get("query", "")
        limit = int(params.get("limit", 5))
        results = await connector.search(query, limit=limit)
        return {"results": [r.to_dict() for r in results]}

    if operation == "fetch":
        resource_id = params.get("resource_id", "")
        resource = await connector.fetch(resource_id)
        return {"resource": resource.to_dict() if resource is not None else None}

    if operation == "act":
        action_type_raw = params.get("action_type", "")
        try:
            action_type = ActionType(action_type_raw)
        except ValueError:
            raise ValueError(
                f"Unknown action_type {action_type_raw!r}. "
                f"Valid values: {[at.value for at in ActionType]}"
            ) from None
        action = Action(
            action_type=action_type,
            connector_id=connector_id,
            resource_id=params.get("resource_id"),
            params=params.get("params") or {},
        )
        result = await connector.act(action)
        return result.to_dict()

    raise ValueError(
        f"Unsupported connector operation {operation!r}. Supported: 'search', 'fetch', 'act'"
    )


# ---------------------------------------------------------------------------
# Registration hook (matches the pattern used by memory.py / runtime.py)
# ---------------------------------------------------------------------------


def register(server: Any) -> None:
    """Register all connector-backed MCP tools onto *server*.

    Follows the same ``register(server)`` contract as every other bundle in
    ``navig/mcp/tools/``.  Failures in individual connector manifests are
    logged and skipped; existing tools remain unaffected.
    """
    if not hasattr(server, "tools"):
        server.tools = {}

    tool_list = list_connector_tools()
    server.tools.update(
        {
            t["name"]: {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            }
            for t in tool_list
        }
    )

    if tool_list:
        logger.debug(
            "MCP connector bridge registered %d tool(s): %s",
            len(tool_list),
            [t["name"] for t in tool_list],
        )
    else:
        logger.debug("MCP connector bridge: no connectors registered, no tools added.")
