"""Compact routed MCP surface — explicit domain tools over the existing handlers.

The full NAVIG catalogue is large (155 tools on Windows, 131 on Linux). A client that
must choose among >100 tools pays context and selection cost, and directory evaluators
score it as badly scoped. This module exposes a **15-tool default surface**: each tool is
a domain with an ``action`` enum, dispatching to the *existing* registered handler for
that operation. Nothing is reimplemented, no tool name from a caller ever reaches the
registry, and the full catalogue remains one flag away (``--profile full``).

Design rules enforced here and in the tests:

* one canonical implementation per operation — actions map to original tool names;
* every action argument set is validated against the original tool's own schema, so
  "which parameter is required" cannot drift from the operation it calls;
* a read-only router may only contain originals that are actually annotated read-only;
* the safety level used by the approval gate is the original operation's level, not the
  router's: a safe read inside a mixed router does not ask for approval, a dangerous
  action does;
* the mapping is a closed table (``ROUTER_SPECS`` + provider regex + registry
  membership) — no ``getattr``/import by caller-supplied string;
* a router never swallows the original result: the handler's dict/list/error sentinel
  comes back unchanged.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from navig.mcp.validation import summarise, validate_arguments

KIND_READ = "read"
KIND_MIXED = "mixed"
KIND_ACT = "act"

_PROVIDER_RE = re.compile(r"^[a-z0-9_]+$")

#: Router-level safety is the maximum of its actions; the gate then resolves the
#: *original* operation per call (see ``gate_target``), so a read inside a mixed router
#: is not gated. The static value still makes annotations/coverage honest.
_LEVELS = {"safe": 0, "moderate": 1, "dangerous": 2}


def _max_level(levels: list[str]) -> str:
    if not levels:
        return "safe"
    return max(levels, key=lambda level: _LEVELS.get(level, 2))


@dataclass(frozen=True)
class ActionSpec:
    """One router action.

    ``original`` is the registered tool that implements it. A connector action is
    dynamic: ``connector`` names the capability (search/fetch/act) and the provider is
    validated against installed handler names. ``renames`` maps a router-facing argument
    to the original argument name (health keeps its native ``connector_id``).
    """

    action: str
    original: str | None = None
    connector: str | None = None
    renames: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RouterSpec:
    name: str
    title: str
    kind: str
    summary: str
    actions: tuple[ActionSpec, ...]
    open_world: bool = False


# ---------------------------------------------------------------------------
# The compact surface — fifteen domains, one action enum each.
# ---------------------------------------------------------------------------

ROUTER_SPECS: dict[str, RouterSpec] = {
    "navig_fleet": RouterSpec(
        name="navig_fleet",
        title="Fleet: hosts, apps and context",
        kind=KIND_READ,
        summary=(
            "Inspect the infrastructure this NAVIG instance is configured for: SSH hosts, "
            "the apps behind them, recent errors/context and database connections. Read-only. "
            "Use navig_knowledge for documentation and navig_runtime for missions; "
            "navig_run_command is only exposed by the full catalogue. Returns host/app/database "
            "entries (names, addresses, paths, status); pass a name to host_info/app_info."
        ),
        actions=(
            ActionSpec("list_hosts", "navig_list_hosts"),
            ActionSpec("host_info", "navig_host_info"),
            ActionSpec("list_apps", "navig_list_apps"),
            ActionSpec("app_info", "navig_app_info"),
            ActionSpec("context", "navig_get_context"),
            ActionSpec("list_databases", "navig_list_databases"),
        ),
    ),
    "navig_knowledge": RouterSpec(
        name="navig_knowledge",
        title="Knowledge: docs, wiki and space context",
        kind=KIND_READ,
        summary=(
            "Search and read NAVIG's local knowledge: product documentation, wiki pages, and "
            "the linked files a Space exposes. Read-only. Use navig_recall for what was said or "
            "decided earlier, navig_fleet for infrastructure inventory. Returns matching "
            "passages/pages with their source path; pass a path to read_wiki_page, or a Space "
            "id to the space_* actions."
        ),
        actions=(
            ActionSpec("search_docs", "navig_search_docs"),
            ActionSpec("search_wiki", "navig_search_wiki"),
            ActionSpec("list_wiki_pages", "navig_list_wiki_pages"),
            ActionSpec("read_wiki_page", "navig_read_wiki_page"),
            ActionSpec("space_current", "navig_space_current"),
            ActionSpec("space_related", "navig_space_related"),
            ActionSpec("space_context", "navig_space_context"),
            ActionSpec("space_read", "navig_space_read"),
        ),
    ),
    "navig_recall": RouterSpec(
        name="navig_recall",
        title="Recall: past work and decisions",
        kind=KIND_READ,
        summary=(
            "Search the owner's recorded past: conversations, decisions, tables, open questions, "
            "forgotten ideas and source excerpts, every result cited. Read-only. Use "
            "navig_knowledge for docs/wiki and navig_memory for durable facts. Returns records "
            "with ids and citations; pass an id to get/related/timeline or a citation to "
            "source_excerpt."
        ),
        actions=(
            ActionSpec("search", "recall_search"),
            ActionSpec("get", "recall_get"),
            ActionSpec("ask", "recall_ask"),
            ActionSpec("related", "recall_related"),
            ActionSpec("timeline", "recall_timeline"),
            ActionSpec("decisions", "recall_decisions"),
            ActionSpec("tables", "recall_tables"),
            ActionSpec("source_excerpt", "recall_source_excerpt"),
            ActionSpec("open_questions", "recall_open_questions"),
            ActionSpec("forgotten_ideas", "recall_forgotten_ideas"),
        ),
    ),
    "navig_memory": RouterSpec(
        name="navig_memory",
        title="Memory: read facts",
        kind=KIND_READ,
        summary=(
            "Read NAVIG's durable key-fact memory: matching facts with their ids/scores, or "
            "counts. Read-only. Use navig_memory_update to create/change/retire a fact, and "
            "navig_recall for conversation history. Returns matching facts with ids and scores, "
            "or {total, active, deleted, superseded} counts."
        ),
        actions=(
            ActionSpec("retrieve", "memory_key_facts_retrieve"),
            ActionSpec("stats", "memory_key_facts_stats"),
        ),
    ),
    "navig_runtime": RouterSpec(
        name="navig_runtime",
        title="Runtime: nodes and missions",
        kind=KIND_READ,
        summary=(
            "Read NAVIG runtime state: registered Nodes, Missions, ExecutionReceipts and a "
            "Node's TrustScore. Read-only. Use navig_runtime_action to create or advance a "
            "Mission. Returns nodes/missions/receipts with ids and state; pass a node_id to "
            "trust_score or a mission id to receipt listing."
        ),
        actions=(
            ActionSpec("list_nodes", "navig_runtime_list_nodes"),
            ActionSpec("list_missions", "navig_runtime_list_missions"),
            ActionSpec("list_receipts", "navig_runtime_list_receipts"),
            ActionSpec("trust_score", "navig_runtime_trust_score"),
        ),
    ),
    "navig_automation": RouterSpec(
        name="navig_automation",
        title="Automation: Bay and blocks",
        kind=KIND_READ,
        summary=(
            "Browse the NAVIG Harbor Bay catalogue and installed Blocks, including a block's "
            "typed inputs, ordered steps and verify criterion. Read-only. Use "
            "navig_automation_apply to acquire an item or run a block. Returns items/specs with "
            "slugs, ids, risk and install state."
        ),
        actions=(
            ActionSpec("bay_list", "navig_bay_list"),
            ActionSpec("block_list", "navig_block_list"),
            ActionSpec("block_show", "navig_block_show"),
        ),
    ),
    "navig_web": RouterSpec(
        name="navig_web",
        title="Web: search, fetch and crawl",
        kind=KIND_MIXED,
        summary=(
            "Search and fetch the public web, or run Firecrawl search/scrape/crawl. Outbound "
            "network access; Firecrawl usage is billable and crawl starts a short-lived job. "
            "Use navig_connectors for authenticated provider data and navig_knowledge for local "
            "docs. Returns ranked results, page content/metadata, or crawl job pages."
        ),
        open_world=True,
        actions=(
            ActionSpec("search", "navig_web_search"),
            ActionSpec("fetch", "navig_web_fetch"),
            ActionSpec("firecrawl_search", "firecrawl_search"),
            ActionSpec("firecrawl_scrape", "firecrawl_scrape"),
            ActionSpec("firecrawl_crawl", "firecrawl_crawl"),
        ),
    ),
    "navig_browser_debug": RouterSpec(
        name="navig_browser_debug",
        title="Browser (debug): inspect",
        kind=KIND_READ,
        summary=(
            "Inspect a NAVIG-launched Chrome/Edge/Brave/Electron debug browser: live targets, "
            "open tabs, accessibility snapshots with refs, and which browsers are running. "
            "Read-only. Use navig_browser_debug_act to interact with that browser, and "
            "navig_browser_user for the operator's own browser. Returns ports/tabs/tree entries; "
            "pass a ref to the act router."
        ),
        open_world=True,
        actions=(
            ActionSpec("targets", "cdp_targets"),
            ActionSpec("tabs", "cdp_tabs"),
            ActionSpec("snapshot", "cdp_snapshot"),
            ActionSpec("launched", "cdp_launched"),
        ),
    ),
    "navig_browser_user": RouterSpec(
        name="navig_browser_user",
        title="Browser (user): inspect and act",
        kind=KIND_MIXED,
        summary=(
            "Work with the operator's own browser through Dock: list tabs, read snapshots/text, "
            "read console/network (the first call starts capture), screenshot, or act "
            "(open/click/type/scroll) under a visible overlay. Screenshots write a file. Use "
            "navig_browser_debug for a NAVIG-launched debug browser; this never silently falls "
            "back to one. Returns tab ids, page text/tree, capture entries, or per-step results."
        ),
        open_world=True,
        actions=(
            ActionSpec("status", "dock_status"),
            ActionSpec("tabs", "dock_tabs"),
            ActionSpec("snapshot", "dock_snapshot"),
            ActionSpec("text", "dock_text"),
            ActionSpec("screenshot", "dock_screenshot"),
            ActionSpec("console", "dock_console"),
            ActionSpec("network", "dock_network"),
            ActionSpec("open", "dock_open"),
            ActionSpec("act", "dock_act"),
            ActionSpec("release", "dock_release"),
        ),
    ),
    "navig_connectors": RouterSpec(
        name="navig_connectors",
        title="Connectors: provider reads",
        kind=KIND_READ,
        summary=(
            "Read from connected provider accounts (Gmail, Drive, Slack, GitHub, Notion, Linear, "
            "Supabase, Calendar, …). Read-only; capabilities vary by installed connector. Use "
            "navig_connectors_act to change provider data. Returns provider status, or matching "
            "items with native ids usable by fetch and act."
        ),
        open_world=True,
        actions=(
            ActionSpec("list", "connector_list"),
            ActionSpec("health", "connector_health", renames={"provider": "connector_id"}),
            ActionSpec("search", connector="search"),
            ActionSpec("fetch", connector="fetch"),
        ),
    ),
    "navig_capabilities": RouterSpec(
        name="navig_capabilities",
        title="Capabilities: plugins, accounts and next steps",
        kind=KIND_READ,
        summary=(
            "Discover what this NAVIG install can do beyond the core domains: installed "
            "plugins (media generation, audio, video, social posting, email, phones, …), "
            "the accounts, keys and devices they can use, the exact command for a goal, and "
            "next steps for a file you produced. Read-only. Check here before telling the "
            "operator something is impossible; run a command with navig_run. Returns plugins "
            "with account states, matching command paths, usage, or ranked next steps."
        ),
        actions=(
            ActionSpec("overview", "navig_capabilities_overview"),
            ActionSpec("find", "navig_capabilities_find"),
            ActionSpec("command_help", "navig_capabilities_command_help"),
            ActionSpec("suggest", "navig_capabilities_suggest"),
        ),
    ),
    "navig_memory_update": RouterSpec(
        name="navig_memory_update",
        title="Memory: change facts",
        kind=KIND_ACT,
        summary=(
            "Create, update or soft-delete a durable key fact in NAVIG memory. State-changing "
            "and audited. Use navig_memory to read facts first. Returns the stored fact id and "
            "status; forgetting is a soft delete."
        ),
        actions=(
            ActionSpec("remember", "memory_key_facts_remember"),
            ActionSpec("update", "memory_key_facts_update"),
            ActionSpec("forget", "memory_key_facts_forget"),
        ),
    ),
    "navig_runtime_action": RouterSpec(
        name="navig_runtime_action",
        title="Runtime: mission actions",
        kind=KIND_ACT,
        summary=(
            "Create a Mission or advance its state (start/succeed/fail/cancel/timeout). "
            "State-changing; mission_action requires approval. Use navig_runtime to read "
            "missions and receipts first. Returns the Mission with its new state."
        ),
        actions=(
            ActionSpec("create_mission", "navig_runtime_create_mission"),
            # The original operation has its own `action` parameter (start/succeed/…),
            # which collides with the router discriminator. It is exposed as `transition`
            # and renamed back on dispatch — explicit, documented, never a silent drop.
            ActionSpec(
                "mission_action",
                "navig_runtime_mission_action",
                renames={"transition": "action"},
            ),
        ),
    ),
    "navig_automation_apply": RouterSpec(
        name="navig_automation_apply",
        title="Automation: acquire and apply",
        kind=KIND_ACT,
        summary=(
            "Acquire a Harbor Bay item or apply an installed Block end-to-end. State-changing: "
            "may install content or run steps; a Block needs its step approvals and resolves "
            "secrets only from env/vault. Use navig_automation to inspect first. Returns the "
            "execution receipt or the acquisition action."
        ),
        open_world=True,
        actions=(
            ActionSpec("bay_acquire", "navig_bay_acquire"),
            ActionSpec("block_apply", "navig_block_apply"),
        ),
    ),
    "navig_browser_debug_act": RouterSpec(
        name="navig_browser_debug_act",
        title="Browser (debug): act",
        kind=KIND_ACT,
        summary=(
            "Act in a NAVIG-launched debug browser: launch/new/navigate/click/type/key/scroll/"
            "move/screenshot/switch, detach/stop, or evaluate/inject/login. State-changing and "
            "high-risk; injected scripts persist until removed and login fills a real credential "
            "(never returned). Use navig_browser_debug to inspect and get refs first. Returns "
            "the dispatch result; re-snapshot to verify."
        ),
        open_world=True,
        actions=(
            ActionSpec("new", "cdp_new"),
            ActionSpec("navigate", "cdp_navigate"),
            ActionSpec("click", "cdp_click"),
            ActionSpec("type", "cdp_type"),
            ActionSpec("key", "cdp_key"),
            ActionSpec("scroll", "cdp_scroll"),
            ActionSpec("move", "cdp_move"),
            ActionSpec("switch", "cdp_switch"),
            ActionSpec("screenshot", "cdp_screenshot"),
            ActionSpec("detach", "cdp_detach"),
            ActionSpec("stop", "cdp_stop"),
            ActionSpec("launch", "cdp_launch"),
            ActionSpec("eval", "cdp_eval"),
            ActionSpec("login", "cdp_login"),
            ActionSpec("inject", "cdp_inject"),
        ),
    ),
    "navig_connectors_act": RouterSpec(
        name="navig_connectors_act",
        title="Connectors: provider writes",
        kind=KIND_ACT,
        summary=(
            "Change data in a connected provider account: reply/create/update/delete/archive/"
            "label/send/move, whichever the provider supports. State-changing and dangerous; "
            "approval required. Use navig_connectors to find the resource id first. Returns "
            "success plus the affected resource, or success:false with the reason."
        ),
        open_world=True,
        actions=(ActionSpec("act", connector="act"),),
    ),
    "navig_run": RouterSpec(
        name="navig_run",
        title="Run an installed NAVIG command",
        kind=KIND_ACT,
        summary=(
            "Run one installed NAVIG CLI command (plugins included) with a list of "
            "arguments: generate media, process audio or video, check a device, prepare or "
            "publish a post. State-changing; publishing, sending and deleting commands "
            "require approval, read commands do not. Use navig_capabilities find/suggest to "
            "get the exact path first. Returns exit code, stdout/stderr, and parsed JSON when "
            "the command printed JSON."
        ),
        open_world=True,
        actions=(ActionSpec("run", "navig_run_cli"),),
    ),
}

#: The exact advertised names, in a stable order.
COMPACT_TOOL_NAMES: tuple[str, ...] = tuple(ROUTER_SPECS)


def _providers(server: Any, capability: str) -> list[str]:
    """Installed provider ids that serve *capability* (never user-supplied)."""
    pattern = re.compile(rf"^connector_([a-z0-9_]+)_{capability}$")
    found = []
    for name in getattr(server, "_tool_handlers", {}):
        match = pattern.match(name)
        if match:
            found.append(match.group(1))
    return sorted(found)


def _provider_property(server: Any, capabilities: tuple[str, ...]) -> dict[str, Any]:
    per_cap = {cap: _providers(server, cap) for cap in capabilities}
    union = sorted({provider for values in per_cap.values() for provider in values})
    description = (
        "Connector provider id. "
        + "; ".join(
            f"{cap}: {', '.join(values) if values else 'none installed'}"
            for cap, values in per_cap.items()
        )
        + ". Unknown providers and unsupported capabilities are refused with the supported list."
    )
    prop: dict[str, Any] = {"type": "string", "description": description}
    if union:
        prop["enum"] = union
    return prop


def _original_schema(server: Any, original: str) -> dict[str, Any] | None:
    tool = getattr(server, "tools", {}).get(original)
    schema = tool.get("inputSchema") if isinstance(tool, dict) else None
    return schema if isinstance(schema, dict) else None


def _connector_schema(action: ActionSpec) -> dict[str, Any] | None:
    from navig.mcp.tools import connectors  # noqa: PLC0415 - avoids a cycle at import time

    if action.action == "search":
        return connectors._search_input_schema()
    if action.action == "fetch":
        return connectors._fetch_input_schema()
    if action.action == "act":
        return connectors._act_input_schema()
    return None


def _schema_for_action(server: Any, action: ActionSpec) -> dict[str, Any] | None:
    if action.connector:
        return _connector_schema(action)
    if action.action == "health":
        return {
            "type": "object",
            "properties": {"provider": _provider_property(server, ("search", "fetch", "act"))},
            "required": ["provider"],
        }
    if action.original:
        return _original_schema(server, action.original)
    return None


def _type_key(spec: dict[str, Any]) -> str:
    kind = spec.get("type")
    if isinstance(kind, list):
        return "|".join(sorted(str(item) for item in kind))
    return str(kind)


def _merge_properties(
    merged: dict[str, dict[str, Any]],
    usage: dict[str, list[str]],
    problems: list[str],
    router: str,
    action: str,
    schema: dict[str, Any],
    provider: dict[str, Any] | None,
) -> None:
    for name, spec in (schema.get("properties") or {}).items():
        if not isinstance(spec, dict):
            continue
        usage.setdefault(name, []).append(action)
        if name not in merged:
            merged[name] = copy.deepcopy(spec)
            continue
        current = merged[name]
        if _type_key(current) != _type_key(spec):
            problems.append(
                f"{router}.{action}: parameter {name!r} type {_type_key(spec)} conflicts with "
                f"{_type_key(current)} from {usage[name][:-1]}"
            )
            continue
        if spec.get("enum") and current.get("enum"):
            shared = [item for item in current["enum"] if item in spec["enum"]]
            if shared:
                current["enum"] = shared
        for bound in ("minimum", "maximum", "minLength", "maxLength"):
            if bound in spec and spec[bound] != current.get(bound):
                current.pop(bound, None)
        if "default" in spec and current.get("default") not in (None, spec["default"]):
            current.pop("default", None)
    if provider is not None:
        name = "provider"
        usage.setdefault(name, []).append(action)
        if name not in merged:
            merged[name] = copy.deepcopy(provider)
        else:
            # Provider is composed once across capabilities in the caller; keep the
            # union of enums rather than dropping a capability's providers.
            existing = set(merged[name].get("enum") or [])
            incoming = set(provider.get("enum") or [])
            if existing or incoming:
                merged[name]["enum"] = sorted(existing | incoming)
            if provider.get("description"):
                merged[name]["description"] = provider["description"]


def _rename_schema(
    schema: dict[str, Any] | None, renames: Mapping[str, str]
) -> dict[str, Any] | None:
    """Expose an original argument under a router-facing name (original → router)."""
    if not schema or not renames:
        return schema
    clone = copy.deepcopy(schema)
    properties = clone.get("properties") or {}
    for original_name, router_name in renames.items():
        if original_name in properties:
            properties[router_name] = properties.pop(original_name)
    required = clone.get("required") or []
    clone["required"] = [renames.get(name, name) for name in required]
    return clone


def _router_renames(action: ActionSpec) -> dict[str, str]:
    """original argument name -> router argument name."""
    return {original: router for router, original in action.renames.items()}


def build_router_tool(server: Any, spec: RouterSpec) -> dict[str, Any]:
    """Build the MCP tool definition for one router from the original schemas."""
    properties: dict[str, dict[str, Any]] = {}
    usage: dict[str, list[str]] = {}
    required_by_action: dict[str, list[str]] = {}
    problems: list[str] = []
    provider = (
        _provider_property(server, (spec.actions[0].connector,))
        if spec.actions and spec.actions[0].connector == "act"
        else None
    )
    if spec.name in {"navig_connectors", "navig_connectors_act"}:
        capabilities = tuple(
            sorted({action.connector for action in spec.actions if action.connector})
        )
        provider = _provider_property(server, capabilities)

    for action in spec.actions:
        schema = _rename_schema(_schema_for_action(server, action), _router_renames(action))
        if schema is not None:
            _merge_properties(properties, usage, problems, spec.name, action.action, schema, None)
            required = schema.get("required") or []
            required_by_action[action.action] = [name for name in required if name != "provider"]
        else:
            required_by_action[action.action] = []
        if provider is not None and (action.connector or action.action == "health"):
            # Provider is merged once at the end so every capability's enum is unioned.
            pass

    if provider is not None:
        properties["provider"] = provider
        for action in spec.actions:
            if action.connector or action.action == "health":
                required_by_action[action.action] = ["provider", *required_by_action[action.action]]

    action_enum = [action.action for action in spec.actions]
    action_lines: list[str] = []
    for action in spec.actions:
        summary = ""
        if action.connector:
            summary = f"{action.connector} via a connected provider"
        elif action.original:
            original = getattr(server, "tools", {}).get(action.original) or {}
            text = str(original.get("description") or original.get("title") or action.action)
            summary = text.split(". ")[0][:180]
        required = required_by_action.get(action.action) or []
        action_lines.append(
            f"{action.action}: {summary or 'see the operation'} (required: "
            f"{', '.join(required) if required else 'none'})"
        )
    required_text = "; ".join(
        f"{name}: {', '.join(required) if required else 'none'}"
        for name, required in required_by_action.items()
    )
    properties["action"] = {
        "type": "string",
        "enum": action_enum,
        "description": (
            "Operation to perform. Required parameters per action: " + required_text + ". "
            "What each action does: " + " | ".join(action_lines)
        ),
    }
    for name, spec_prop in properties.items():
        if name == "action":
            continue
        actions = usage.get(name, [])
        if actions:
            suffix = "Used by: " + ", ".join(actions) + "."
            existing = spec_prop.get("description") or ""
            spec_prop["description"] = f"{existing} {suffix}".strip()

    if problems:
        raise ValueError(
            f"compact surface {spec.name!r} has incompatible parameters across actions: "
            + "; ".join(problems)
        )

    annotations: dict[str, Any] = {
        "readOnlyHint": spec.kind == KIND_READ,
        "destructiveHint": spec.kind != KIND_READ,
        "openWorldHint": bool(spec.open_world),
        "idempotentHint": spec.kind == KIND_READ,
    }
    return {
        "name": spec.name,
        "title": spec.title,
        "description": spec.summary,
        "annotations": annotations,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": ["action"],
            "additionalProperties": False,
        },
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def router_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """A structured, error-shaped tool result (never a phantom success)."""
    return {"error": code, "message": message, "isError": True, **extra}


def _action_spec(spec: RouterSpec, action: Any) -> ActionSpec | None:
    if not isinstance(action, str):
        return None
    for candidate in spec.actions:
        if candidate.action == action:
            return candidate
    return None


def _target_for(
    server: Any, action: ActionSpec, provider: str | None
) -> tuple[str | None, str | None]:
    """Resolve (original tool name, error message) for one action."""
    if action.connector:
        if not provider or not _PROVIDER_RE.match(provider):
            return None, "provider is required for this action"
        original = f"connector_{provider}_{action.connector}"
        if original not in getattr(server, "_tool_handlers", {}):
            supported = _providers(server, action.connector)
            return None, (
                f"provider {provider!r} does not support {action.connector!r}"
                + (f"; supported: {', '.join(supported)}" if supported else "; none installed")
            )
        return original, None
    if action.action == "health":
        return action.original, None
    return action.original, None


def action_args(
    action: ActionSpec, arguments: Mapping[str, Any], provider: str | None
) -> dict[str, Any]:
    """The argument dict handed to the original handler (router names → original names)."""
    rename_to_original = dict(action.renames)  # router name -> original name
    router_args = {
        key: value for key, value in arguments.items() if key not in {"action", "provider"}
    }
    if action.action == "health" and provider is not None:
        return {"connector_id": provider}
    return {rename_to_original.get(key, key): value for key, value in router_args.items()}


def dispatch(server: Any, spec: RouterSpec, arguments: Mapping[str, Any]) -> Any:
    """Run one router action through the original registered handler."""
    action = arguments.get("action")
    action_spec = _action_spec(spec, action)
    if action_spec is None:
        return router_error(
            "unknown_action",
            f"{spec.name}: unknown action {action!r}",
            valid_actions=[candidate.action for candidate in spec.actions],
        )
    provider = arguments.get("provider")
    if provider is not None and not isinstance(provider, str):
        return router_error("invalid_arguments", f"{spec.name}: provider must be a string")
    provider = provider.strip() if isinstance(provider, str) else None
    original, problem = _target_for(server, action_spec, provider)
    if original is None:
        return router_error(
            "unsupported_action",
            f"{spec.name}.{action}: {problem}",
            supported_providers=_providers(server, action_spec.connector or ""),
        )
    handlers = getattr(server, "_tool_handlers", {})
    handler = handlers.get(original)
    if handler is None:
        return router_error(
            "tool_unavailable",
            f"{spec.name}.{action}: the underlying operation {original!r} is not registered",
        )

    # Validate the arguments the CLIENT sees, not the original names: an action that
    # exposes `transition` must complain about `transition`, not about the original
    # operation's `action`. The handler then receives the original schema's names.
    original_schema = _original_schema(server, original) or {}
    router_renames = _router_renames(action_spec)
    if action_spec.action == "health":
        # provider was validated against the registry above; the original's
        # connector_id is derived from it.
        problems = []
    elif action_spec.connector:
        # Connector provider actions keep the generated tool's native arguments.
        problems = validate_arguments(original_schema, action_args(action_spec, arguments, None))
    else:
        router_schema = _rename_schema(original_schema, router_renames) or {}
        router_args = {key: value for key, value in arguments.items() if key != "action"}
        problems = validate_arguments(router_schema, router_args)
    if problems:
        return router_error(
            "invalid_arguments",
            f"{spec.name}.{action}: {summarise(problems)}",
            field=problems[0].get("field"),
            problems=problems,
        )
    call_args = action_args(action_spec, arguments, provider)
    return handler(server, call_args)


def gate_target(server: Any, router: str, arguments: Mapping[str, Any]) -> tuple[str, str] | None:
    """(original tool, safety level) for the approval gate; None for an unknown action."""
    spec = ROUTER_SPECS.get(router)
    if spec is None:
        return None
    action_spec = _action_spec(
        spec, arguments.get("action") if isinstance(arguments, Mapping) else None
    )
    if action_spec is None:
        return None
    provider = arguments.get("provider") if isinstance(arguments, Mapping) else None
    provider = provider.strip() if isinstance(provider, str) else None
    original, _problem = _target_for(server, action_spec, provider)
    if original is None:
        return None
    if original == "navig_run_cli":
        return run_cli_gate(arguments)
    level = getattr(server, "_tool_safety", {}).get(original, "safe")
    return original, level


def run_cli_gate(arguments: Mapping[str, Any]) -> tuple[str, str]:
    """Gate target for ``navig_run``: the exact command path at its own level.

    The approval prompt and audit log then name ``cli:navig social upload``, not a
    generic runner, and a read such as ``navig mobile devices`` is not gated at all. A path
    that is not an installed command resolves to ``dangerous`` — the handler refuses it
    anyway, so the gate must never be the weaker of the two checks.
    """
    from navig.capabilities.safety import classify
    from navig.mcp.tools.capabilities import resolve_run_target

    path, _problem = resolve_run_target(
        arguments.get("path") if isinstance(arguments, Mapping) else None
    )
    if path is None:
        return "navig_run_cli", "dangerous"
    return f"cli:{path}", classify(path)


def _make_handler(spec: RouterSpec):
    def handler(server: Any, arguments: dict[str, Any]) -> Any:
        return dispatch(server, spec, arguments or {})

    return handler


def _constituent_tools(spec: RouterSpec, server: Any) -> list[str]:
    names: list[str] = []
    for action in spec.actions:
        if action.connector:
            names.extend(
                _providers(server, action.connector)
                and [
                    f"connector_{provider}_{action.connector}"
                    for provider in _providers(server, action.connector)
                ]
            )
        elif action.original:
            names.append(action.original)
    if spec.name == "navig_connectors":
        names.extend(["connector_list", "connector_health"])
        for capability in ("search", "fetch"):
            names.extend(
                f"connector_{provider}_{capability}" for provider in _providers(server, capability)
            )
    return names


def _assert_read_router_is_read_only(spec: RouterSpec, server: Any) -> None:
    if spec.kind != KIND_READ:
        return
    tools = getattr(server, "tools", {})
    for name in _constituent_tools(spec, server):
        annotations = (tools.get(name) or {}).get("annotations") or {}
        if (
            annotations.get("readOnlyHint") is not True
            or annotations.get("destructiveHint") is not False
        ):
            raise ValueError(
                f"compact surface {spec.name!r} is declared read-only but {name!r} is not "
                "annotated read-only — move it to an action router or fix the annotation"
            )


def register_compact_surface(server: Any) -> dict[str, dict[str, Any]]:
    """Register the fifteen router tools and make only them visible.

    The full handler map stays in place: routers, resources and tests resolve originals
    through it, while ``_visible_tools`` is what a client is allowed to call/list.
    """
    router_actions: dict[str, dict[str, str]] = {}
    for name, spec in ROUTER_SPECS.items():
        _assert_read_router_is_read_only(spec, server)
        if name in getattr(server, "tools", {}):
            raise ValueError(f"mini surface collision: {name!r} already exists")
        tool = build_router_tool(server, spec)
        server.tools[name] = tool
        server._tool_handlers[name] = _make_handler(spec)
        levels: list[str] = []
        table: dict[str, str] = {}
        for action in spec.actions:
            if action.connector:
                for provider in _providers(server, action.connector):
                    original = f"connector_{provider}_{action.connector}"
                    levels.append(getattr(server, "_tool_safety", {}).get(original, "safe"))
                table[action.action] = action.connector
            else:
                original = action.original or ""
                levels.append(getattr(server, "_tool_safety", {}).get(original, "safe"))
                table[action.action] = original
        server._tool_safety[name] = _max_level(levels)
        router_actions[name] = table
        if any(
            original in getattr(server, "_concurrent_tools", set())
            for original in table.values()
            if original
        ):
            server._concurrent_tools.add(name)
    server._router_actions = router_actions
    server._visible_tools = set(ROUTER_SPECS)
    return router_actions


def advertised_tools(server: Any) -> dict[str, dict[str, Any]]:
    """The tool definitions a client is currently allowed to see and call."""
    tools = getattr(server, "tools", {})
    if not getattr(server, "_restricted", False):
        return tools
    visible = getattr(server, "_visible_tools", set())
    return {name: tool for name, tool in tools.items() if name in visible}


def is_visible(server: Any, name: str) -> bool:
    """Whether a client may call *name* on this exposure."""
    if not getattr(server, "_restricted", False):
        return True
    return name in getattr(server, "_visible_tools", set())


def apply_read_only_surface(server: Any) -> set[str]:
    """Keep only genuinely read-only tools visible (unauthenticated exposure).

    Filters the CURRENT advertised surface, not the registry: on the compact profile the
    full catalogue is registered for dispatch but hidden, and an exposure filter must not
    accidentally reveal it.
    """
    current = advertised_tools(server)
    keep = {
        name
        for name, tool in current.items()
        if (
            (tool.get("annotations") or {}).get("readOnlyHint") is True
            and (tool.get("annotations") or {}).get("destructiveHint") is False
        )
    }
    server._visible_tools = keep
    server._restricted = True
    return keep
