"""MCP Tool Bridge — Connector Auto-Discovery

Exposes every connector registered in ``ConnectorRegistry`` as MCP-callable
tools, zero duplication.

**Naming convention:**  ``connector_{id}_search`` / ``connector_{id}_fetch``
/ ``connector_{id}_act``  — one tool per enabled capability.

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
import atexit
import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Any

from navig.connectors.registry import get_connector_registry
from navig.connectors.types import Action, ActionType
from navig.mcp.annotations import connector_annotations

logger = logging.getLogger("navig.mcp.tools.connectors")

# Thread pool for bridging async connector calls into the synchronous
# tool-dispatch chain used by MCPProtocolHandler._execute_tool().
# asyncio.run() creates a fresh event loop in the worker thread so there
# is no conflict with any event loop that may be running in the main thread.
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp_conn")
# Ensure the pool is shut down cleanly on interpreter exit.
atexit.register(_POOL.shutdown, wait=False)


# ---------------------------------------------------------------------------
# Input-schema builders (per capability)
# ---------------------------------------------------------------------------


#: Hard ceiling on ``limit`` for a connector search. The handler clamps to the same bound
#: (``_clamp_limit``), so the schema cannot promise 50 while the runtime happily asks a
#: provider for 10 000 rows.
MAX_SEARCH_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 5


def _search_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query, e.g. 'invoices from Acme last month'",
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum number of results to return ({1}-{MAX_SEARCH_LIMIT})"
                ),
                "default": DEFAULT_SEARCH_LIMIT,
                "minimum": 1,
                "maximum": MAX_SEARCH_LIMIT,
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
                "minLength": 1,
                "description": (
                    "Native id of the item, exactly as returned by the matching "
                    "connector_<id>_search result"
                ),
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
                "enum": [action.value for action in ActionType],
                "description": (
                    "Write to perform. The provider must support it — a value outside its "
                    "supported set is refused with success: false, not silently applied."
                ),
            },
            "resource_id": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Native id of the item to change, when the action targets an existing "
                    "item (reply, update, delete, archive, label, move). Omit it for create/send."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Action-specific payload, e.g. {to, subject, body} for send, "
                    "{body} for reply, {add_labels, remove_labels} for label"
                ),
            },
        },
        "required": ["action_type"],
    }


def _clamp_limit(value: Any) -> int:
    """Coerce ``limit`` to an int inside ``1..MAX_SEARCH_LIMIT``.

    The schema states the bound, but a client is free to ignore a schema — and the old
    handler forwarded whatever arrived straight to the provider (``limit=10000`` became
    ten thousand rows from Gmail). Clamping is what makes the advertised bound true.
    """
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = DEFAULT_SEARCH_LIMIT
    return max(1, min(limit, MAX_SEARCH_LIMIT))


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


# ---------------------------------------------------------------------------
# Per-capability definitions — title, description, annotations
# ---------------------------------------------------------------------------
#
# A connector exposes up to three verbs and the name alone does not tell an agent which to
# reach for, so each description answers four questions: what the verb does, what comes
# back, which sibling continues the workflow, and what it will NOT do.
#
# They are built here from the manifest's capability flags — deliberately NOT from
# ``manifest.description``. That text describes the CONNECTOR ("Search, read, send, and
# manage Gmail messages"), so appending it to a search-only tool advertised send and
# delete on a schema that cannot reach them. That is the defect the published
# tool-definition evaluation scored 2.2-2.8/5: search tools claiming write capabilities
# while annotated ``destructiveHint: true``.


@dataclass(frozen=True)
class _Provider:
    """The manifest fields a tool definition needs, read once behind the guard.

    A hostile or half-built manifest can raise from any property (``display_name`` is a
    property on real connectors), and that must skip ONE connector, never take the whole
    catalogue down — which is why the fields are snapshotted in the ``try`` instead of
    being touched later by each builder.
    """

    display_name: str
    can_search: bool
    can_fetch: bool
    can_act: bool


def _title(provider: _Provider, operation: str) -> str:
    """Human label for one connector capability, e.g. ``Search Gmail``."""
    display = provider.display_name
    return {
        "search": f"Search {display}",
        "fetch": f"Read one {display} item",
        "act": f"Change {display}",
    }[operation]


def _search_description(cid: str, provider: _Provider) -> str:
    parts = [
        f"Find {provider.display_name} resources matching a query. Read-only: nothing is changed.",
        "Returns matching items with their native id, title, preview, url and timestamp; "
        "an empty list means nothing matched.",
    ]
    if provider.can_fetch:
        parts.append(f"Pass an item's id to connector_{cid}_fetch to read it in full.")
    if provider.can_act:
        parts.append(f"To change data instead, use connector_{cid}_act.")
    if not provider.can_fetch and not provider.can_act:
        # Previews are all this connector exposes. Saying so, and naming the tool that
        # explains why, is more useful than a search result the caller cannot follow up.
        parts.append(
            "This connector exposes previews only — use connector_list to compare its "
            "capability flags with the connectors that can read or change items."
        )
    return " ".join(parts)


def _fetch_description(cid: str, provider: _Provider) -> str:
    return (
        f"Read one {provider.display_name} item by the id returned from connector_{cid}_search. "
        "Read-only: returns the item's full content and metadata. An id the connected account "
        "cannot see surfaces as an error or a null resource — never as a different item."
    )


def _act_description(cid: str, provider: _Provider) -> str:
    """The write verb, routed back to the read that supplies ``resource_id``.

    The batch-1 rewrite got the capability claims right but stopped at the verb: an agent
    told to "change data" still has to know where the id comes from. Each clause is emitted
    only when the provider actually has that capability, so an act-only provider is never
    pointed at a tool that does not exist.
    """
    if provider.can_search and provider.can_fetch:
        find = f"Use connector_{cid}_search or connector_{cid}_fetch to find it first."
    elif provider.can_search:
        find = f"Use connector_{cid}_search to find it first."
    elif provider.can_fetch:
        find = f"Use connector_{cid}_fetch to read a known id first."
    else:
        find = "Use connector_list to compare capabilities first."
    return (
        f"Change data in {provider.display_name}: reply, create, update, delete, archive, "
        "label, send or move — whichever this provider supports. Pass resource_id for an "
        "existing item and params for the payload; returns success plus the affected "
        f"resource, or success: false with the reason. {find}"
    )


_DEFINITION_BUILDERS: dict[str, Any] = {
    "search": (_search_input_schema, _search_description),
    "fetch": (_fetch_input_schema, _fetch_description),
    "act": (_act_input_schema, _act_description),
}


def _connector_tool(cid: str, provider: _Provider, operation: str) -> dict[str, Any]:
    """One complete MCP tool definition for a connector capability.

    Complete on purpose: ``register()`` copies title and annotations through, and
    ``navig.mcp.annotations.annotate_tools`` only fills blanks (``setdefault``), so the
    generator that knows the provider's capability matrix owns the verdict.
    """
    schema_fn, description_fn = _DEFINITION_BUILDERS[operation]
    return {
        "name": f"connector_{cid}_{operation}",
        "title": _title(provider, operation),
        "description": description_fn(cid, provider),
        "annotations": connector_annotations(operation),
        "inputSchema": schema_fn(),
    }


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
            "name": "connector_list",
            "title": "List connectors",
            "description": (
                "List all registered connectors with their id, display name, domain, "
                "status, and capability flags (can_search / can_fetch / can_act). Returns "
                "one row per connector; use connector_health to probe one, or call its "
                "connector_<id>_search tool to read its data."
            ),
            "inputSchema": _list_connectors_input_schema(),
        },
        {
            "name": "connector_health",
            "title": "Check connector health",
            "description": (
                "Probe the health of a specific connector and return latency + ok status. "
                "Use connector_list to find a valid connector_id — a health result says "
                "whether the connection works, not whether it has data."
            ),
            "inputSchema": _health_input_schema(),
        },
    ]

    for cid, cls in reg.all_classes().items():
        try:
            manifest = cls.manifest
            # Snapshot every field the builders need INSIDE the guard: a manifest property
            # that raises must cost us one connector, not the catalogue.
            provider = _Provider(
                display_name=manifest.display_name,
                can_search=bool(manifest.can_search),
                can_fetch=bool(manifest.can_fetch),
                can_act=bool(manifest.can_act),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Connector %r: manifest access failed (%s) — skipping MCP tool registration",
                cid,
                exc,
            )
            continue

        for operation, enabled in (
            ("search", provider.can_search),
            ("fetch", provider.can_fetch),
            ("act", provider.can_act),
        ):
            if enabled:
                tools.append(_connector_tool(cid, provider, operation))

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

    Expected *tool_name* format:  ``"connector_{id}_{operation}"``
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
    # Format: connector_{id}_{operation} where operation in {search, fetch, act}
    _OPERATIONS = ("search", "fetch", "act")
    connector_id = operation = ""
    if tool_name.startswith("connector_"):
        remainder = tool_name[len("connector_"):]
        for op in _OPERATIONS:
            if remainder.endswith(f"_{op}"):
                connector_id = remainder[: -(len(op) + 1)]
                operation = op
                break
    if not connector_id or not operation:
        if tool_name.startswith("connector_"):
            parts = tool_name.split("_")
            # parts[0]="connector", parts[-1]=attempted operation, middle=connector id
            if len(parts) >= 3 and parts[-1] not in _OPERATIONS:
                raise ValueError(f"Unsupported connector operation: {parts[-1]!r}")
        raise ValueError(
            f"Invalid connector tool name {tool_name!r}. "
            "Expected format: 'connector_<id>_<operation>'"
        )

    reg = registry or get_connector_registry()
    # Raises ConnectorNotFoundError if not registered — intentional.
    connector = reg.get(connector_id)

    # Hydrate the connector with its stored vault token (transparently
    # refreshes if expired). Without this, OAuth connectors raise
    # "no access token" because the token lives in the vault, not the instance.
    if operation in ("search", "fetch", "act") and getattr(connector.manifest, "requires_oauth", True):
        from navig.connectors.auth_manager import ConnectorAuthManager
        from navig.connectors.errors import ConnectorAuthError

        injected = False
        try:
            injected = await ConnectorAuthManager().inject_token(connector)
        except Exception as _inj_exc:  # noqa: BLE001
            logger.warning("Token injection for %s failed: %s", connector_id, _inj_exc)

        if not injected:
            # Fail fast, and say why. Previously the return value was discarded and
            # execution continued tokenless, so the agent got whatever confusing
            # error the connector or the upstream API produced ("no access token",
            # a bare 401) instead of "connect it in Settings".
            #
            # Worse, search/fetch/act are wrapped by the connector CircuitBreaker,
            # so those auth failures were charged to the breaker's failure budget:
            # three calls to a *disconnected* connector opened it for 30 s, and the
            # user's first call after finally connecting was rejected with "circuit
            # breaker is open". An auth problem is not an upstream outage, so it
            # must never reach the wrapped method.
            raise ConnectorAuthError(
                connector_id,
                "not connected, or its stored token could not be refreshed — "
                "connect it in Settings → Connectors",
            )

    if operation == "search":
        query = params.get("query", "")
        limit = _clamp_limit(params.get("limit", DEFAULT_SEARCH_LIMIT))
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
        future = _POOL.submit(lambda: asyncio.run(handle_connector_call(tool_name, args)))
        try:
            return future.result(timeout=30)
        except concurrent.futures.TimeoutError:
            # Best-effort: prevents queued-but-not-yet-started work from
            # running.  Cannot interrupt a thread that is already executing;
            # the worker will run to completion (or hang) independently.
            future.cancel()
            logger.warning("Connector tool %r timed out after 30s", tool_name)
            raise

    _handler.__name__ = f"_connector_{tool_name}"
    return _handler


def _tool_connector_list(server: Any, args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """Return summary rows for every registered connector.

    Capability flags (``can_search`` / ``can_fetch`` / ``can_act``) are
    included directly via ``ConnectorRegistry.list_all()``.
    """
    return {"connectors": get_connector_registry().list_all()}


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
    future = _POOL.submit(lambda: asyncio.run(connector.health_check()))
    try:
        health = future.result(timeout=15)
        return health.to_dict()
    except concurrent.futures.TimeoutError:
        future.cancel()
        logger.warning("connector.health(%s) timed out after 15s", connector_id)
        return {"error": f"Health check for '{connector_id}' timed out", "isError": True}
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

    # Register connector CLASSES so they appear in the MCP tool list even when
    # the CLI has not been invoked (e.g. pure MCP server startup). Manifests
    # only: listing tools needs the capability flags, not OAuth provider
    # configs — and the full load resolves app credentials through the vault,
    # which costs 5-6 s on first open. Token hydration/refresh at CALL time
    # registers the provider config lazily (ConnectorAuthManager.get_access_token).
    from navig.connectors.bootstrap import ensure_manifests_loaded

    ensure_manifests_loaded()

    tool_list = list_connector_tools()

    # Schema dict — used by tools/list to advertise the tool surface. Every field the
    # generator set travels with it: title and annotations are part of the definition, not
    # decoration (a dropped annotation makes annotate_tools fall back to its conservative
    # default and the read verbs reappear as destructive).
    _DEFINITION_FIELDS = ("name", "title", "description", "annotations", "inputSchema")
    server.tools.update(
        {
            t["name"]: {key: t[key] for key in _DEFINITION_FIELDS if key in t}
            for t in tool_list
        }
    )

    # Dispatch table — used by MCPProtocolHandler._execute_tool() to call tools.
    # Meta-tools have dedicated sync handlers; per-connector operations go
    # through the async bridge (_make_sync_connector_handler).
    handlers: dict[str, Any] = {
        "connector_list": _tool_connector_list,
        "connector_health": _tool_connector_health,
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

    # Safety classification consulted by the approval gate (navig.mcp_server._gate_tool).
    # These tools are generated per connector, so they cannot be listed statically — the
    # classification has to be derived from the capability, here, or it does not happen
    # at all. It did not: every connector tool ran ungated AND unaudited, while cdp_eval
    # was gated as a credential-exfiltration vector.
    #
    #   *_act    → dangerous. `_act_input_schema` covers reply/create/update/delete/
    #              archive/label/send/move, so this is "send mail as the operator",
    #              "delete the message", "write the row" — outward-facing and often
    #              irreversible.
    #   *_search → moderate. Reads private third-party data (mailbox, drive, bank,
    #              Stripe). Not "safe": safe means an operator never needs to know.
    #   *_fetch  → moderate, for the same reason.
    safety: dict[str, str] = {
        "connector_list": "safe",     # names and capability flags only
        "connector_health": "safe",   # reachability, no content
    }
    for t in tool_list:
        name = t["name"]
        if name in safety:
            continue
        if name.endswith("_act"):
            safety[name] = "dangerous"
        elif name.endswith(("_search", "_fetch")):
            safety[name] = "moderate"
        else:
            # An unrecognised capability suffix must not silently become "safe".
            safety[name] = "dangerous"
    # A module's register() must be self-sufficient: register_all_tools creates this
    # dict, but a direct `module.register(server)` call (tests, a plugin host) does not,
    # and assuming it exists raised AttributeError. cdp.py already guarded; these did not.
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update(safety)
