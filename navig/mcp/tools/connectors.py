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

import asyncio
import concurrent.futures
import logging
from typing import Any

from navig.connectors.registry import get_connector_registry
from navig.connectors.types import Action, ActionType

logger = logging.getLogger("navig.mcp.tools.connectors")

# Thread pool for bridging async connector calls into the synchronous
# tool-dispatch chain used by MCPProtocolHandler._execute_tool().
# asyncio.run() creates a fresh event loop in the worker thread so there
# is no conflict with any event loop that may be running in the main thread.
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp_conn")


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


def _list_connectors_input_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "required": []}


def _health_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "connector_id": {
                "type": "string",
                "description": "ID of the connector to probe (e.g. 'gmail', 'perplexity')",
            }
        },
        "required": ["connector_id"],
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
    tools: list[dict[str, Any]] = [
        {
            "name": "connector.list",
            "description": (
                "List all registered connectors with their id, display name, domain, "
                "status, and capability flags (can_search / can_fetch / can_act)."
            ),
            "inputSchema": _list_connectors_input_schema(),
        },
        {
            "name": "connector.health",
            "description": (
                "Probe the health of a specific connector and return latency + ok status."
            ),
            "inputSchema": _health_input_schema(),
        },
    ]

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
# Sync tool handlers (bridge async → thread pool, matching memory.py pattern)
# ---------------------------------------------------------------------------


def _make_sync_connector_handler(tool_name: str) -> Any:
    """Return a sync ``_tool_handlers`` callable for *tool_name*.

    ``asyncio.run()`` creates a fresh event loop inside the pool thread, which
    is safe regardless of whether the caller is inside an already-running loop.
    The 30-second timeout prevents hung connectors from blocking the MCP server.
    """

    def _handler(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
        future = _POOL.submit(asyncio.run, handle_connector_call(tool_name, args))
        return future.result(timeout=30)

    _handler.__name__ = f"_connector_{tool_name.replace('.', '_')}"
    return _handler


def _tool_connector_list(server: Any, args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """Return summary rows for every registered connector."""
    reg = get_connector_registry()
    rows = reg.list_all()
    # Enrich with capability flags from the class manifest
    classes = reg.all_classes()
    for row in rows:
        cls = classes.get(row["id"])
        if cls is not None:
            try:
                m = cls.manifest
                row["can_search"] = m.can_search
                row["can_fetch"] = m.can_fetch
                row["can_act"] = m.can_act
            except Exception:  # noqa: BLE001
                pass
    return {"connectors": rows}


def _tool_connector_health(server: Any, args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """Probe a connector's health and return the ``HealthStatus`` dict."""
    connector_id = (args.get("connector_id") or "").strip()
    if not connector_id:
        return {"error": "connector_id is required", "isError": True}
    reg = get_connector_registry()
    try:
        connector = reg.get(connector_id)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "isError": True}
    try:
        future = _POOL.submit(asyncio.run, connector.health_check())
        health = future.result(timeout=15)
        return health.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.warning("connector.health(%s) failed: %s", connector_id, exc)
        return {"error": str(exc), "isError": True}


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
    if not hasattr(server, "_tool_handlers"):
        server._tool_handlers = {}

    tool_list = list_connector_tools()

    # Schema dict — used by tools/list to advertise the tool surface
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

    # Dispatch table — used by MCPProtocolHandler._execute_tool() to call tools.
    # Meta-tools have dedicated sync handlers; per-connector operations go
    # through the async bridge (_make_sync_connector_handler).
    handlers: dict[str, Any] = {
        "connector.list": _tool_connector_list,
        "connector.health": _tool_connector_health,
    }
    for t in tool_list:
        name = t["name"]
        if name not in handlers:  # skip meta-tools already added above
            handlers[name] = _make_sync_connector_handler(name)
    server._tool_handlers.update(handlers)

    if tool_list:
        logger.debug(
            "MCP connector bridge registered %d tool(s): %s",
            len(tool_list),
            [t["name"] for t in tool_list],
        )
    else:
        logger.debug("MCP connector bridge: no connectors registered, no tools added.")
