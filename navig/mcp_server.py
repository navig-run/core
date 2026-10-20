"""NAVIG MCP Server

Model Context Protocol server that exposes NAVIG's capabilities to AI assistants.
This allows Copilot, Claude, and other MCP-compatible assistants to:
- Query hosts, apps, and database configurations
- Search the wiki knowledge base
- Execute commands on remote servers
- Access project context

Usage:
    navig mcp serve                          # Start MCP server (stdio mode)
    navig mcp serve --transport websocket    # Start WebSocket server on port 3001
    navig mcp serve --port 3001             # Same as above (port implies websocket)

VS Code Integration:
    Add to .vscode/mcp.json or VS Code settings
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from navig import console_helper as ch
from navig.config import ConfigManager
from navig.mcp.validation import summarise, validate_arguments

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransportContext:
    """How this server is exposed — the input to NAVIG's transport-aware policy.

    ``local`` means a trusted single-operator surface (stdio, or a loopback listener
    the operator explicitly opted into). ``remote`` means the wire is reachable by
    somebody other than the desktop user (a network bind, or a stdio server declared
    remote because a proxy exposes it). ``token_present`` says an authentication
    credential protects the surface; it never by itself authorises dangerous actions.
    """

    mode: str = "stdio"
    host: str = ""
    exposure: str = "local"
    token_present: bool = True
    insecure_local: bool = False


def is_loopback(host: str) -> bool:
    """True only for the loopback names/addresses — never for a forwarded header."""
    import ipaddress

    value = (host or "").strip().lower().strip("[]")
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _remote_dangerous_allowed(config: ConfigManager) -> bool:
    """Explicit operator policy for dangerous operations over a remote exposure."""
    try:
        value = config.get("mcp.remote_dangerous", "deny")
    except Exception:  # noqa: BLE001 - an unreadable policy must deny, not allow
        return False
    return str(value).strip().lower() == "approve"


def _tool_error(
    code: str,
    detail: str,
    *,
    field: str | None = None,
    problems: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a tool error envelope that both a human and a model can act on.

    The text used to be a bare sentence (``Unknown tool: x`` / ``Tool error: ...``), and
    a model could not tell WHICH argument was wrong or what type was expected. The body
    is now JSON — ``{"error": code, "detail": ..., "field": ...}`` — while the human
    prefix stays, because existing tests and users read it.
    """
    body: dict[str, Any] = {"error": code, "detail": detail}
    if field:
        body["field"] = field
    if problems:
        body["problems"] = problems
    return {
        "isError": True,
        "content": [{"type": "text", "text": json.dumps(body, indent=2)}],
    }


def _result_signals_error(result: Any) -> bool:
    """True when a tool's RETURN value signals failure by convention.

    NAVIG's MCP tools report failure by RETURNING a sentinel, not by raising —
    ``{"error": ...}``, ``{"ok": False}``, ``{"success": False}``, or (some
    already, believing the dispatch honored it) ``{"isError": True}``. Without
    recognizing that convention the dispatch wraps the failure as a NORMAL result,
    so a failed real-infra op (a permission-denied write, a failed block apply)
    reaches the MCP client as a phantom success — the protocol ``isError`` flag,
    the one machine-readable failure signal, never set.

    Only the UNAMBIGUOUS dict conventions are honored: a truthy ``isError`` /
    ``error``, or ``ok``/``success`` explicitly ``False``. Deliberately NOT a bare
    ``returncode`` (a non-zero exit is normal for grep/diff/test commands) nor any
    string heuristic (would false-positive on content that opens with "Error"). A
    tool wanting a non-zero exit surfaced as an error should return ``{"ok": False}``.
    """
    if not isinstance(result, dict):
        return False
    if result.get("isError"):
        return True
    if result.get("error"):
        return True
    return result.get("ok") is False or result.get("success") is False


class MCPProtocolHandler:
    """Handles MCP JSON-RPC protocol over stdio."""

    def __init__(
        self,
        only: list[str] | tuple[str, ...] | None = None,
        profile: str = "full",
        transport: TransportContext | None = None,
    ):
        #: Tool bundles to serve (``navig mcp serve --only``); ``None`` serves them all.
        self._only = tuple(only) if only else None
        #: ``full`` (legacy catalogue) or ``default`` (the compact routed surface).
        #: ``--only`` keeps its historical meaning: a restricted full catalogue.
        self._profile = "full" if only else profile
        #: Where this server is exposed; drives S2's fail-closed policy.
        self._transport = transport or TransportContext()
        self.tools: dict[str, dict[str, Any]] = {}
        #: Names a client may list and call. Full profile = every tool; compact =
        #: the routers; unauthenticated exposure = read-only tools only.
        self._visible_tools: set[str] = set()
        #: True once the advertised surface is deliberately narrower than the registry.
        self._restricted = False
        #: router name -> {action: original tool or capability} (see navig.mcp.routing)
        self._router_actions: dict[str, dict[str, str]] = {}
        self.resources: dict[str, dict[str, Any]] = {}
        self.prompts: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable] = {}
        self._running = False
        self._config = ConfigManager()
        from navig.mcp.files_policy import policy_from_config

        # S3: containment for desktop_filesystem, driven by exposure + mcp.files.*
        self._files_policy = policy_from_config(self._config, self._transport)
        # Tools a stdio loop may run off the reader thread (bundles opt in), so a slow
        # call never blocks the next request — or the client's notifications/cancelled.
        self._concurrent_tools: set[str] = set()
        self._cancel_hooks: dict[Any, list[Callable[[], None]]] = {}
        self._cancelled: set[Any] = set()
        self._local = threading.local()
        self._out_lock = threading.Lock()

        # Register built-in methods
        self._setup_protocol_handlers()
        self._setup_navig_tools()
        self._setup_navig_resources()
        self._apply_exposure_surface()

    def _setup_navig_tools(self):
        """Register NAVIG tools for MCP — the full catalogue, then the profile view."""
        self.tools = {}
        self._tool_handlers = {}
        # Per-tool safety classification consulted by the approval gate before a
        # tool runs. Absent → "safe" (never gated). Bundles populate this in
        # their register() (see navig.mcp.tools.cdp).
        self._tool_safety: dict[str, str] = {}
        from navig.mcp.tools import register_all_tools

        only = getattr(self, "_only", None)
        if only:
            register_all_tools(self, only=only)
        else:
            register_all_tools(self)  # the long-standing one-argument contract (tests patch it)

        # The compact surface is built ON TOP of the full registration: its handlers
        # call the originals directly, so the full handler map must exist first.
        if getattr(self, "_profile", "full") == "default":
            from navig.mcp.routing import register_compact_surface

            register_compact_surface(self)
            self._restricted = True
        else:
            self._visible_tools = set(self.tools)

    def _apply_exposure_surface(self) -> None:
        """Drop executable actions from an unauthenticated network exposure."""
        transport = getattr(self, "_transport", TransportContext())
        if transport.mode == "stdio" or transport.token_present or transport.insecure_local:
            return
        from navig.mcp.routing import apply_read_only_surface

        kept = apply_read_only_surface(self)
        self._restricted = True
        logger.warning(
            "MCP exposed without a token on %s: advertising %d read-only tool(s) only. "
            "Pass --token (recommended) or --insecure-local for the full local surface.",
            transport.host or "loopback",
            len(kept),
        )

    def _setup_protocol_handlers(self):
        """Setup MCP protocol method handlers."""
        self._handlers = {}
        self._handlers["initialize"] = self._handle_initialize
        self._handlers["initialized"] = self._handle_initialized
        self._handlers["tools/list"] = self._handle_tools_list
        self._handlers["tools/call"] = self._handle_tools_call
        self._handlers["resources/list"] = self._handle_resources_list
        # Part of the Resources capability, with no flag of its own — a client calls it as
        # soon as the resources panel opens (see ``_handle_resources_templates_list``).
        self._handlers["resources/templates/list"] = self._handle_resources_templates_list
        self._handlers["resources/read"] = self._handle_resources_read
        self._handlers["prompts/list"] = self._handle_prompts_list
        self._handlers["prompts/get"] = self._handle_prompts_get
        self._handlers["ping"] = self._handle_ping
        self._handlers["notifications/cancelled"] = self._handle_cancelled

    def _setup_navig_resources(self):
        """Register NAVIG resources for MCP."""
        if getattr(self, "_only", None):
            # The resources read the inventory/wiki/agent tools — a narrow server does not
            # carry those, so it offers no resources rather than ones that always fail.
            self.resources = {}
            return
        self.resources = {
            "navig://config/hosts": {
                "uri": "navig://config/hosts",
                "name": "NAVIG Hosts Configuration",
                "description": "All configured SSH hosts",
                "mimeType": "application/json",
            },
            "navig://config/apps": {
                "uri": "navig://config/apps",
                "name": "NAVIG Apps Configuration",
                "description": "All configured applications",
                "mimeType": "application/json",
            },
            "navig://wiki": {
                "uri": "navig://wiki",
                "name": "NAVIG Wiki",
                "description": "Project knowledge base",
                "mimeType": "text/markdown",
            },
            "navig://context": {
                "uri": "navig://context",
                "name": "NAVIG Context",
                "description": "Current system state and recent errors",
                "mimeType": "application/json",
            },
            "navig://agent/status": {
                "uri": "navig://agent/status",
                "name": "NAVIG Agent Status",
                "description": "Agent runtime status and configuration",
                "mimeType": "application/json",
            },
            "navig://agent/goals": {
                "uri": "navig://agent/goals",
                "name": "NAVIG Agent Goals",
                "description": "Autonomous goal list and progress",
                "mimeType": "application/json",
            },
            "navig://agent/remediation": {
                "uri": "navig://agent/remediation",
                "name": "NAVIG Agent Remediation",
                "description": "Remediation actions and recent remediation logs",
                "mimeType": "application/json",
            },
            "navig://agent/learning": {
                "uri": "navig://agent/learning",
                "name": "NAVIG Agent Learning Report",
                "description": "Latest error pattern analysis report",
                "mimeType": "application/json",
            },
            "navig://agent/service": {
                "uri": "navig://agent/service",
                "name": "NAVIG Agent Service Status",
                "description": "Service installer/status integration",
                "mimeType": "application/json",
            },
            "agent://status": {
                "uri": "agent://status",
                "name": "Agent Status",
                "description": "Alias for NAVIG agent runtime status",
                "mimeType": "application/json",
            },
            "agent://goals": {
                "uri": "agent://goals",
                "name": "Agent Goals",
                "description": "Alias for agent goals",
                "mimeType": "application/json",
            },
            "agent://remediation": {
                "uri": "agent://remediation",
                "name": "Agent Remediation",
                "description": "Alias for remediation actions",
                "mimeType": "application/json",
            },
            "agent://learning/patterns": {
                "uri": "agent://learning/patterns",
                "name": "Agent Learning Patterns",
                "description": "Alias for learning report",
                "mimeType": "application/json",
            },
            "agent://service": {
                "uri": "agent://service",
                "name": "Agent Service",
                "description": "Alias for service status",
                "mimeType": "application/json",
            },
            # ── Runtime Contracts ──────────────────────────────────────
            "navig://runtime/nodes": {
                "uri": "navig://runtime/nodes",
                "name": "NAVIG Runtime Nodes",
                "description": "All registered Node identities",
                "mimeType": "application/json",
            },
            "navig://runtime/missions": {
                "uri": "navig://runtime/missions",
                "name": "NAVIG Runtime Missions",
                "description": "Recent Missions with lifecycle state",
                "mimeType": "application/json",
            },
            "navig://runtime/receipts": {
                "uri": "navig://runtime/receipts",
                "name": "NAVIG Execution Receipts",
                "description": "Audit trail of completed Mission executions",
                "mimeType": "application/json",
            },
        }

    # =========================================================================
    # MCP Protocol Handlers
    # =========================================================================

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle MCP initialize request.

        Three things here are load-bearing for every marketplace listing:

        * ``serverInfo.version`` must be NAVIG's real version. It used to be a literal
          "1.0.0", so a client (and every directory that scans us) reported 1.0.0 for a
          3.27.x server — the one number a reviewer checks against PyPI.
        * ``capabilities`` must only announce what is actually served. ``prompts`` was
          advertised while ``prompts/list`` always returned an empty list, which shows up
          in clients as a permanently empty Prompts panel.
        * ``protocolVersion`` should echo the client's revision when we implement it, and
          fall back to our known-good one otherwise.
        """
        # Who is calling (e.g. "claude-code") — tools that act visibly for the user, like
        # the Dock's browser overlay, name the agent instead of a generic "an agent".
        info = (params or {}).get("clientInfo")
        if isinstance(info, dict) and isinstance(info.get("name"), str):
            self.client_name = info["name"][:60]

        known = {"2024-11-05", "2025-03-26", "2025-06-18"}
        requested = (params or {}).get("protocolVersion") if isinstance(params, dict) else None
        protocol = requested if requested in known else "2024-11-05"

        capabilities: dict[str, Any] = {"tools": {}, "resources": {}}
        if getattr(self, "prompts", None):
            capabilities["prompts"] = {}

        return {
            "protocolVersion": protocol,
            "capabilities": capabilities,
            "serverInfo": {"name": "navig-mcp-server", "version": self._server_version()},
            # MCP `instructions`: a client shows this to the model before it picks a tool.
            # Without it the model meets a list of names and no context; with it, the
            # first call is usually the right domain instead of a guess. Kept short on
            # purpose — it costs tokens on every session.
            "instructions": (
                (
                    "NAVIG exposes the machine it runs on through fifteen domain tools; each "
                    "takes an `action` argument. Start with navig_fleet's context/list_hosts, "
                    "search navig_knowledge for docs/wiki, navig_recall for past decisions and "
                    "navig_memory for durable facts. State-changing domains are separate "
                    "(*_update/*_action/*_apply and browser act tools) and are gated by NAVIG's "
                    "approval layer; prefer read tools while exploring. `navig mcp serve "
                    "--profile full` exposes the complete catalogue."
                )
                if getattr(self, "_profile", "full") == "default"
                else (
                    "NAVIG exposes the machine it runs on: SSH host and app inventory, a local "
                    "wiki, persistent memory, and (on Windows) desktop and CDP browser control. "
                    "Start with navig_get_context or navig_list_hosts to see what is configured. "
                    "Tools whose names end in _act, _write, _delete, _exec, _install or _apply "
                    "change state and are gated by NAVIG's approval layer; prefer the read-only "
                    "tools while exploring."
                )
            ),
        }

    @staticmethod
    def _server_version() -> str:
        """NAVIG's version, resolved from the installed package (never a literal)."""
        try:
            from navig import __version__ as navig_version

            return str(navig_version)
        except Exception:  # pragma: no cover - a broken install still has to answer
            return "0.0.0-unknown"

    def _handle_initialized(self, params: dict[str, Any]) -> None:
        """Handle MCP initialized notification."""
        return None

    def _handle_cancelled(self, params: dict[str, Any]) -> None:
        """``notifications/cancelled``: run the cancel hooks the request registered."""
        rid = (params or {}).get("requestId")
        if rid is None:
            return None
        self._cancelled.add(rid)
        for hook in self._cancel_hooks.pop(rid, []):
            try:
                hook()
            except Exception as exc:  # noqa: BLE001 — a hook failing must not stop the others
                logger.debug("MCP cancel hook failed for %s: %s", rid, exc)
        return None

    def current_request_id(self) -> Any:
        """The JSON-RPC id of the request this thread is serving (stdio), else None."""
        return getattr(self._local, "request_id", None)

    def on_cancel(self, hook: Callable[[], None]) -> None:
        """Run *hook* if the client cancels the request being served on this thread."""
        rid = self.current_request_id()
        if rid is not None:
            self._cancel_hooks.setdefault(rid, []).append(hook)

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ping request."""
        return {}

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the tools this exposure is allowed to advertise."""
        from navig.mcp.routing import advertised_tools

        return {"tools": list(advertised_tools(self).values())}

    def advertised_tools(self) -> dict[str, dict[str, Any]]:
        """The tool definitions a client can currently list and call."""
        from navig.mcp.routing import advertised_tools

        return advertised_tools(self)

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call, after checking the arguments against its schema.

        Validation runs BEFORE the approval gate and the handler, because both assume
        well-formed arguments: a wrong type used to reach the handler and come back as
        ``'int' object has no attribute 'strip'``, which names neither the tool nor the
        argument. An unknown argument is refused rather than ignored — a client that
        sends ``hostname`` instead of ``host`` must be told, not silently answered about
        something else.
        """
        tool_name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}

        tool = self.tools.get(tool_name) if isinstance(tool_name, str) else None
        if tool is None:
            return _tool_error("unknown_tool", f"Unknown tool: {tool_name}")

        from navig.mcp.routing import is_visible

        if not is_visible(self, tool_name):
            # A hidden full-catalogue tool is as unknown as a nonexistent one: the
            # compact profile is a real boundary, not a filtered listing.
            return _tool_error("unknown_tool", f"Unknown tool: {tool_name}")

        problems = validate_arguments(tool.get("inputSchema") or {}, arguments)
        if problems:
            return _tool_error(
                "invalid_arguments",
                f"{tool_name}: {summarise(problems)}",
                field=problems[0].get("field"),
                problems=problems,
            )

        try:
            result = self._execute_tool(tool_name, arguments)
            # A tool may hand over ready MCP content blocks (an image, compact JSON) under
            # `_mcp_content`; the rest of the dict still decides isError below.
            ready = result.get("_mcp_content") if isinstance(result, dict) else None
            if isinstance(ready, list) and ready:
                envelope: dict[str, Any] = {"content": ready}
            else:
                envelope = {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]
                }

            # Structured output: only for tools that DECLARE an outputSchema, and only in
            # the shape that schema promises (list tools wrap in {"items": [...]}). A
            # client that understands it can skip re-parsing the text block; one that
            # does not loses nothing.
            declared = tool.get("outputSchema")
            if isinstance(declared, dict):
                if isinstance(result, dict):
                    envelope["structuredContent"] = result
                elif isinstance(result, list) and declared.get("properties", {}).get("items"):
                    envelope["structuredContent"] = {"items": result}

            # A tool that signals failure by RETURNING a sentinel (not raising) must
            # surface as a protocol-level error, not a phantom success — an MCP
            # client keyed on isError would otherwise record a failed op as done.
            if _result_signals_error(result):
                envelope["isError"] = True
            return envelope
        except Exception as e:
            return _tool_error("tool_error", f"Tool error: {str(e)}")

    def _handle_resources_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return list of available resources."""
        return {"resources": list(self.resources.values())}

    def _handle_resources_templates_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the parameterised resource URIs a client may construct.

        NAVIG serves none: every resource here is a fixed URI (``_setup_navig_resources``),
        so the honest answer is an empty list — a template that resolves to nothing would
        be worse than no template at all.

        The METHOD still has to exist. ``initialize`` advertises the ``resources``
        capability, and ``resources/templates/list`` has no capability flag of its own, so
        a spec-following client treats it as part of Resources and calls it the moment that
        panel opens (Glama's hosted Inspector, Claude Desktop, every directory that
        introspects us). While the handler was missing they got ``-32601 Method not
        found`` for a capability the server had just claimed — a healthy 3.27.3 server
        looked broken, and the Inspector's Resource Templates tab showed only the error.
        """
        return {"resourceTemplates": []}

    def _handle_resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read a resource."""
        uri = params.get("uri")

        if uri not in self.resources:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Unknown resource: {uri}"}],
            }

        try:
            content = self._read_resource(uri)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": self.resources[uri].get("mimeType", "text/plain"),
                        "text": content,
                    }
                ]
            }
        except Exception as e:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Resource error: {str(e)}"}],
            }

    def _handle_prompts_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return list of available prompts."""
        return {"prompts": []}

    def _handle_prompts_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get a specific prompt."""
        return {"messages": []}

    # =========================================================================
    # Tool Implementations
    # =========================================================================

    def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool and return result."""
        handler = getattr(self, "_tool_handlers", {}).get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        self._gate_tool(tool_name, arguments)
        return handler(self, arguments)

    def _gate_tool(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Route powerful MCP tools through the approval gate before they run.

        The stdio dispatcher is synchronous, so this uses the sync bridge
        ``approval.check_sync``. Tools classified ``safe`` (the default) skip the
        gate entirely — zero overhead. Dangerous tools (e.g. ``cdp_eval``, which
        can read a password field off a logged-in page) are gated and audited;
        the single-operator default backend approves-with-audit, but an operator
        can install a blocking/interactive backend or a stricter policy.

        Raises PermissionError when the gate denies — surfaced to the caller as a
        tool error by ``_handle_tools_call``.
        """
        safety = getattr(self, "_tool_safety", {}).get(tool_name, "safe")
        original = tool_name
        route: dict[str, Any] = {}
        if getattr(self, "_router_actions", {}).get(tool_name):
            from navig.mcp.routing import gate_target

            target = gate_target(self, tool_name, arguments)
            if target is None:
                # An unknown/invalid router action is reported by the router handler,
                # with the valid action list. It never reaches an original operation.
                return
            original, safety = target
            route = {"router": tool_name, "action": arguments.get("action"), "original": original}
        if safety == "safe":
            return
        transport = getattr(self, "_transport", TransportContext())
        if (
            transport.exposure == "remote"
            and safety == "dangerous"
            and not _remote_dangerous_allowed(self._config)
        ):
            raise PermissionError(
                f"Tool '{tool_name}' is dangerous and this server is exposed remotely; "
                "mcp.remote_dangerous is 'deny'. Set `navig config set mcp.remote_dangerous "
                "approve` only if an operator explicitly authorises unattended high-risk use."
            )
        try:
            from navig.tools.approval import ApprovalDecision, check_sync
        except Exception as exc:  # noqa: BLE001
            # Fail CLOSED: if the approval gate can't load, a dangerous/moderate tool
            # (e.g. cdp_eval, which can read a password field) must NOT run ungated.
            raise PermissionError(
                f"approval gate unavailable ({exc}); refusing '{tool_name}'"
            ) from exc
        # Audit the attempt by arg *keys* only — never log values (a secret could
        # live in cdp_type `text` / cdp_eval `expression`).
        logger.warning(
            "[mcp.gate] %s tool '%s'%s (args: %s) surface=mcp",
            safety,
            tool_name,
            (
                f" -> {original} (router={route.get('router')}, action={route.get('action')})"
                if route
                else ""
            ),
            ",".join(sorted(arguments or {})) or "-",
        )
        decision = check_sync(
            original,
            safety,
            parameters={"arg_keys": sorted(arguments or {})},
            context={"surface": "mcp", **route},
        )
        if decision != ApprovalDecision.APPROVED:
            raise PermissionError(
                f"Tool '{tool_name}' was not approved by the approval gate ({decision.value})."
            )

    def _read_resource(self, uri: str) -> str:
        """Read resource content."""
        if uri == "navig://config/hosts":
            return json.dumps(self._execute_tool("navig_list_hosts", {}), indent=2)
        elif uri == "navig://config/apps":
            return json.dumps(self._execute_tool("navig_list_apps", {}), indent=2)
        elif uri == "navig://wiki":
            pages = self._execute_tool("navig_wiki_list", {})
            if isinstance(pages, dict) and "error" in pages:
                return f"# Wiki\n\n{pages['error']}"
            return "# Wiki Index\n\n" + "\n".join(f"- [{p['title']}]({p['path']})" for p in pages)
        elif uri == "navig://context":
            return json.dumps(self._execute_tool("navig_get_context", {}), indent=2)
        elif uri in ("navig://agent/status", "agent://status"):
            return json.dumps(self._execute_tool("navig_agent_status_get", {}), indent=2)
        elif uri in ("navig://agent/goals", "agent://goals"):
            return json.dumps(self._execute_tool("navig_agent_goal_list", {"limit": 100}), indent=2)
        elif uri in ("navig://agent/remediation", "agent://remediation"):
            return json.dumps(
                self._execute_tool("navig_agent_remediation_list", {"limit": 100}),
                indent=2,
            )
        elif uri in ("navig://agent/learning", "agent://learning/patterns"):
            report_path = config_dir() / "workspace" / "error-patterns.json"
            if report_path.exists():
                return report_path.read_text(encoding="utf-8", errors="replace")
            return json.dumps(
                self._execute_tool("navig_agent_learning_run", {"days": 7, "export": False}),
                indent=2,
            )
        elif uri in ("navig://agent/service", "agent://service"):
            return json.dumps(self._execute_tool("navig_agent_service_status", {}), indent=2)
        elif uri == "navig://runtime/nodes":
            from navig.contracts.store import get_runtime_store

            store = get_runtime_store()
            return json.dumps([n.to_dict() for n in store.list_nodes()], indent=2)
        elif uri == "navig://runtime/missions":
            from navig.contracts.store import get_runtime_store

            store = get_runtime_store()
            return json.dumps([m.to_dict() for m in store.list_missions(limit=50)], indent=2)
        elif uri == "navig://runtime/receipts":
            from navig.contracts.store import get_runtime_store

            store = get_runtime_store()
            return json.dumps([r.to_dict() for r in store.list_receipts(limit=50)], indent=2)
        else:
            raise ValueError(f"Unknown resource: {uri}")

    # =========================================================================
    # Server Loop
    # =========================================================================

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC message."""
        method = message.get("method")
        params = message.get("params", {})
        msg_id = message.get("id")

        if method not in self._handlers:
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            return None

        try:
            result = self._handlers[method](params)
            if msg_id is not None:
                return {"jsonrpc": "2.0", "id": msg_id, "result": result}
            return None
        except Exception as e:
            logger.debug("MCP handler error method=%s: %s", method, e, exc_info=True)
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": str(e)},
                }
            return None

    def _serve_one(self, message: dict[str, Any]) -> None:
        """Handle one message and write its response (thread-safe; drops cancelled ones)."""
        rid = message.get("id")
        self._local.request_id = rid
        try:
            response = self.handle_message(message)
        finally:
            self._local.request_id = None
            self._cancel_hooks.pop(rid, None)
        if rid is not None and rid in self._cancelled:
            self._cancelled.discard(rid)
            return  # the client cancelled it: MCP says no response is sent
        if response:
            with self._out_lock:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

    def _runs_concurrently(self, message: dict[str, Any]) -> bool:
        if message.get("method") != "tools/call" or message.get("id") is None:
            return False
        name = (message.get("params") or {}).get("name")
        return name in self._concurrent_tools

    def run_stdio(self):
        """Run MCP server in stdio mode.

        Opted-in tools (``_concurrent_tools``, e.g. the ``dock_*`` browser tools) run on a
        small pool so the reader keeps reading: the next request and a
        ``notifications/cancelled`` for the running one are handled while it works.
        Everything else stays strictly in order on this thread.
        """
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        self._running = True
        pool: ThreadPoolExecutor | None = None

        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                message = json.loads(line)
                if self._runs_concurrently(message):
                    if pool is None:
                        pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mcp-tool")
                    pool.submit(self._serve_one, message)
                    continue
                self._serve_one(message)

            except json.JSONDecodeError as e:
                sys.stderr.write(f"JSON decode error: {e}\n")
            except Exception as e:
                sys.stderr.write(f"Error: {e}\n")

        if pool is not None:
            # The client went away: cancel what is still running so we exit promptly.
            for rid in list(self._cancel_hooks):
                self._handle_cancelled({"requestId": rid})
            pool.shutdown(wait=True)

    def stop(self):
        """Stop the server."""
        self._running = False


def start_mcp_server(
    mode: str = "stdio",
    port: int = 3001,
    token: str | None = None,
    host: str = "127.0.0.1",
    only: list[str] | tuple[str, ...] | None = None,
    profile: str = "full",
    exposure: str | None = None,
    insecure_local: bool = False,
):
    """Start the NAVIG MCP server.

    Args:
        mode: Server mode — 'stdio', 'websocket', or 'http'
        port: Port for WebSocket/HTTP mode (default 3001)
        token: Optional auth token; auto-generated if None in websocket mode
        host: Bind address for HTTP/WebSocket modes (default 127.0.0.1)
        only: Serve only these tool bundles (e.g. ``["recall"]``); default all
        profile: ``full`` (legacy catalogue) or ``default`` (compact routed surface).
            The CLI and ``python -m navig.mcp_server`` pass ``default``; this function
            keeps ``full`` so programmatic callers/tests are unchanged.
        exposure: ``local`` or ``remote``. Defaults to local for stdio and remote for
            network transports. Declare ``remote`` for a stdio server behind a proxy.
        insecure_local: explicit opt-in to keep the full surface on a loopback
            listener without a token. Never permitted on a non-loopback bind.
    """
    if profile not in {"default", "full"}:
        raise ValueError(f"unknown MCP profile {profile!r}; use 'default' or 'full'")
    if exposure is None:
        # A container/packager can declare the exposure once in the environment, so a
        # platform that overrides the launch command cannot silently downgrade a remote
        # server to local trust. An explicit flag still wins.
        env_exposure = os.environ.get("NAVIG_MCP_EXPOSURE", "").strip().lower()
        if env_exposure:
            if env_exposure not in {"local", "remote"}:
                raise ValueError(
                    f"NAVIG_MCP_EXPOSURE={env_exposure!r} is invalid; use 'local' or 'remote'"
                )
            exposure = env_exposure
        else:
            # Loopback is the operator's own machine. A network bind is remote. A stdio
            # server behind a proxy is declared remote explicitly (--exposure remote).
            exposure = "local" if mode == "stdio" or is_loopback(host) else "remote"
    if exposure not in {"local", "remote"}:
        raise ValueError(f"unknown MCP exposure {exposure!r}; use 'local' or 'remote'")
    if insecure_local and not is_loopback(host):
        raise ValueError(
            "--insecure-local is only allowed on a loopback bind; refusing to expose the "
            f"full surface unauthenticated on {host!r}"
        )
    if mode == "websocket" and token is None:
        # WebSocket always carries a token (generated before the surface/policy is
        # built); a caller cannot reach it without the value printed locally.
        token = secrets.token_urlsafe(32)
    if mode != "stdio":
        if not is_loopback(host) and not token:
            raise ValueError(
                f"refusing to expose the MCP server on {host!r} without --token. Bind "
                "127.0.0.1, or provide a Bearer token (recommended)."
            )
        if exposure == "remote" and not token:
            raise ValueError(
                "exposure=remote requires authentication; pass --token or use stdio behind "
                "an authenticating proxy"
            )
    if only and profile == "default":
        # --only keeps its historical semantics: a restricted full catalogue.
        profile = "full"
    transport = TransportContext(
        mode=mode,
        host=host,
        exposure=exposure,
        token_present=bool(token) or mode == "stdio",
        insecure_local=insecure_local,
    )
    handler = MCPProtocolHandler(only=only, profile=profile, transport=transport)

    if mode == "stdio":
        handler.run_stdio()
    elif mode == "websocket":
        _run_websocket_server(handler, port, token, host=host)
    elif mode == "http":
        _run_http_server(handler, host=host, port=port, token=token)
    else:
        raise ValueError(f"Unknown mode: {mode!r}. Use 'stdio', 'websocket', or 'http'.")


def _build_http_app(
    handler: "MCPProtocolHandler",
    host: str = "127.0.0.1",
    port: int = 3001,
    token: str | None = None,
) -> "Any":
    """Build and return an aiohttp Application for the MCP HTTP transport.

    Extracted from ``_run_http_server`` so the app can be created in unit
    tests without spinning up a real TCP server.

    Args:
        handler: Protocol handler (can be a mock in tests)
        host: Bind host (used only in SSE endpoint-event URL)
        port: Bind port (used only in SSE endpoint-event URL)
        token: Optional Bearer token; ``None`` = open server

    Returns:
        ``aiohttp.web.Application`` instance.
    """
    from aiohttp import web

    session_token = token

    # A wildcard CORS origin on a loopback listener is what makes a browser page able
    # to reach the local server; on a network bind it is never appropriate. The MCP
    # protocol itself is used by non-browser clients, so the token (required for a
    # non-loopback bind) is the real boundary.
    if is_loopback(host):
        _CORS_HEADERS = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept",
        }
    else:
        _CORS_HEADERS = {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept",
        }

    def _check_auth(request: web.Request) -> bool:
        if not session_token:
            return True
        auth = request.headers.get("Authorization", "")
        return hmac.compare_digest(auth, f"Bearer {session_token}")

    async def handle_options(request: web.Request) -> web.Response:
        preflight = {
            **_CORS_HEADERS,
            "Access-Control-Max-Age": "86400",
        }
        if session_token:
            # Allow the browser preflight to send the Bearer header: the actual request
            # still fails without it.
            preflight["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Accept"
        return web.Response(status=200, headers=preflight)

    async def handle_health(request: web.Request) -> web.Response:
        body = json.dumps({"status": "ok", "server": "navig-mcp", "transport": "http"})
        return web.Response(body=body, content_type="application/json", headers=_CORS_HEADERS)

    async def handle_sse(request: web.Request) -> web.StreamResponse:
        """GET /mcp — SSE stream for server-initiated notifications."""
        if not _check_auth(request):
            raise web.HTTPUnauthorized(reason="Invalid or missing Bearer token")

        response = web.StreamResponse(
            status=200,
            headers={
                **_CORS_HEADERS,
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        # Send an endpoint event per MCP Streamable HTTP spec, then keepalives
        mcp_url = f"http://{host}:{port}/mcp"
        await response.write(f"event: endpoint\ndata: {mcp_url}\n\n".encode())
        try:
            while True:
                await asyncio.sleep(25)
                await response.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # expected on client disconnect or shutdown
        return response

    async def handle_post(request: web.Request) -> web.Response:
        """POST /mcp — handle JSON-RPC 2.0 request."""
        if not _check_auth(request):
            err_body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "Unauthorized"},
                    "id": None,
                }
            )
            return web.Response(
                status=401,
                body=err_body,
                content_type="application/json",
                headers=_CORS_HEADERS,
            )

        try:
            body = await request.text()
            if len(body) > 1_048_576:  # 1 MiB guard
                size_err = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32600, "message": "Request body too large"},
                        "id": None,
                    }
                )
                return web.Response(
                    status=413,
                    body=size_err,
                    content_type="application/json",
                    headers=_CORS_HEADERS,
                )
            message = json.loads(body)
        except Exception as exc:
            parse_err = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                    "id": None,
                }
            )
            return web.Response(
                status=400,
                body=parse_err,
                content_type="application/json",
                headers=_CORS_HEADERS,
            )

        response_obj = handler.handle_message(message)
        if response_obj is None:
            # Notification (no id) — acknowledged, no body
            return web.Response(status=202, headers=_CORS_HEADERS)

        return web.Response(
            status=200,
            body=json.dumps(response_obj, default=str),
            content_type="application/json",
            headers=_CORS_HEADERS,
        )

    app = web.Application()
    app.router.add_options("/mcp", handle_options)
    app.router.add_get("/mcp", handle_sse)
    app.router.add_post("/mcp", handle_post)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)  # root alias
    return app


def _run_http_server(
    handler: MCPProtocolHandler,
    host: str = "127.0.0.1",
    port: int = 3001,
    token: str | None = None,
) -> None:
    """Run NAVIG as a Streamable HTTP MCP server.

    Implements the MCP 2024-11-05 Streamable HTTP transport:
      POST /mcp   — JSON-RPC 2.0 request → JSON-RPC 2.0 response
      GET  /mcp   — SSE stream for server-push notifications
      OPTIONS /mcp — CORS preflight
      GET  /health — liveness check

    Perplexity AI integration:
      In Perplexity → Add custom connector → paste the /mcp URL printed below.
      If a token is set, also configure it as a Bearer token in Perplexity.
    """
    try:
        from aiohttp import web  # noqa: F401  (ensure importable)
    except ImportError as _exc:
        ch.error("HTTP transport requires the 'aiohttp' package.")
        ch.info("Install with:  pip install aiohttp")
        raise SystemExit(1) from _exc

    app = _build_http_app(handler, host=host, port=port, token=token)

    async def _serve() -> None:
        from aiohttp import web

        mcp_url = f"http://{host}:{port}/mcp"
        ch.success(f"NAVIG MCP HTTP server listening on {mcp_url}")
        if token:
            ch.info(f"Auth token: {token}")
            ch.dim("Clients must send:  Authorization: Bearer <token>")
        else:
            ch.dim("No authentication required (open server).")
        ch.console.print("")
        ch.console.print("  [bold]Perplexity → Add custom connector → MCP Server URL:[/bold]")
        ch.console.print(f"  [bold green]{mcp_url}[/bold green]")
        ch.console.print("")
        ch.dim("Press Ctrl+C to stop.")

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        try:
            await asyncio.Future()  # run forever
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass  # expected on shutdown signal
        finally:
            await runner.cleanup()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        ch.info("MCP HTTP server stopped.")


def _run_websocket_server(
    handler: MCPProtocolHandler, port: int, token: str | None = None, host: str = "0.0.0.0"
):
    """Start a WebSocket MCP server with optional token auth.

    The server speaks JSON-RPC 2.0 over WebSocket frames.
    Each frame is one JSON-RPC request/response.
    Notifications (no 'id') are fire-and-forget.

    Auth: if token is set, clients must send it as the first message
    or via the ``Authorization`` header (``Bearer <token>``).
    """
    try:
        import websockets
        import websockets.asyncio.server
    except ImportError as _exc:
        ch.error("WebSocket mode requires the 'websockets' package.")
        ch.info("Install with:  pip install websockets")
        raise SystemExit(1) from _exc

    # Generate a session token if not provided
    session_token = token or secrets.token_urlsafe(32)
    authenticated_clients: set = set()
    authenticated_websockets: set = set()

    # --- Event Bridge integration (push pipeline) ---
    from navig.event_bridge import EventBridge, SubscriptionFilter

    event_bridge = EventBridge(debounce_seconds=1.0)

    notification_sources = {
        "agent.status.changed": "navig://agent/status",
        "agent.goal.changed": "navig://agent/goals",
        "agent.remediation.changed": "navig://agent/remediation",
    }
    debounce_seconds = 1.5
    poll_interval_seconds = 1.0
    max_notification_bytes = 131072
    notification_state: dict[str, dict[str, Any]] = {
        topic: {
            "last_seen_digest": None,
            "last_seen_at": None,
            "last_payload": None,
            "last_emitted_digest": None,
        }
        for topic in notification_sources
    }

    async def _broadcast_notification(payload: str) -> None:
        """Broadcast notification to authenticated clients with drop-safe sends."""
        if not authenticated_websockets:
            return

        async def _send_safe(websocket_obj) -> bool:
            try:
                await asyncio.wait_for(websocket_obj.send(payload), timeout=0.25)
                return True
            except Exception:
                return False

        sockets = list(authenticated_websockets)
        results = await asyncio.gather(
            *[_send_safe(ws) for ws in sockets],
            return_exceptions=True,
        )
        for ws, result in zip(sockets, results):
            if result is not True:
                authenticated_websockets.discard(ws)

    async def _notification_loop() -> None:
        """Poll runtime resources and emit debounced MCP notifications on change."""
        import hashlib

        while True:
            await asyncio.sleep(poll_interval_seconds)
            if not authenticated_websockets:
                continue

            now = datetime.now()
            for topic, uri in notification_sources.items():
                try:
                    resource_text = handler._read_resource(uri)
                except Exception:
                    continue

                digest = hashlib.sha256(resource_text.encode("utf-8", errors="replace")).hexdigest()

                state = notification_state[topic]
                if digest != state["last_seen_digest"]:
                    state["last_seen_digest"] = digest
                    state["last_seen_at"] = now

                    params: dict[str, Any] = {
                        "uri": uri,
                        "changed_at": now.isoformat(),
                    }
                    try:
                        params["data"] = json.loads(resource_text)
                    except json.JSONDecodeError:
                        params["text"] = resource_text[:4000]

                    payload_obj = {
                        "jsonrpc": "2.0",
                        "method": topic,
                        "params": params,
                    }
                    payload = json.dumps(payload_obj, default=str)
                    if len(payload.encode("utf-8", errors="replace")) > max_notification_bytes:
                        payload = json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": topic,
                                "params": {
                                    "uri": uri,
                                    "changed_at": now.isoformat(),
                                    "truncated": True,
                                },
                            },
                            default=str,
                        )
                    state["last_payload"] = payload

                # Debounce: only emit after value has stayed unchanged for a short window.
                if (
                    state["last_seen_digest"] is not None
                    and state["last_seen_digest"] != state["last_emitted_digest"]
                    and state["last_seen_at"] is not None
                    and (now - state["last_seen_at"]).total_seconds() >= debounce_seconds
                    and state["last_payload"] is not None
                ):
                    await _broadcast_notification(state["last_payload"])
                    # Also push through EventBridge for filtered delivery
                    from navig.event_bridge import Severity

                    await event_bridge.push_direct(
                        topic=topic,
                        source="mcp.resource_poll",
                        data={"uri": uri, "changed_at": now.isoformat()},
                        severity=Severity.INFO,
                    )
                    state["last_emitted_digest"] = state["last_seen_digest"]

    async def _handle_client(websocket):
        client_id = id(websocket)

        # Check auth header first
        auth_header = websocket.request.headers.get("Authorization", "")
        if auth_header == f"Bearer {session_token}":
            authenticated_clients.add(client_id)
            authenticated_websockets.add(websocket)
            event_bridge.register_client(websocket)

        try:
            async for raw in websocket:
                # If not yet authenticated, expect token as first message
                if session_token and client_id not in authenticated_clients:
                    if raw.strip() == session_token:
                        authenticated_clients.add(client_id)
                        authenticated_websockets.add(websocket)
                        event_bridge.register_client(websocket)
                        ack = json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "result": {"authenticated": True},
                                "id": 0,
                            }
                        )
                        await websocket.send(ack)
                        continue
                    else:
                        err = json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "error": {
                                    "code": -32000,
                                    "message": "Authentication required",
                                },
                                "id": None,
                            }
                        )
                        await websocket.send(err)
                        await websocket.close(4001, "Authentication failed")
                        return

                # Normal JSON-RPC handling
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    err = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "error": {"code": -32700, "message": "Parse error"},
                            "id": None,
                        }
                    )
                    await websocket.send(err)
                    continue

                response = handler.handle_message(message)

                # --- EventBridge: handle subscription requests ---
                method = message.get("method", "")
                msg_id = message.get("id")

                if method == "navig.subscribe" and msg_id is not None:
                    params = message.get("params", {})
                    filt = SubscriptionFilter.from_dict(params)
                    event_bridge.update_client_filter(websocket, filt)
                    sub_resp = {
                        "jsonrpc": "2.0",
                        "result": {"subscribed": True, "filter": filt.to_dict()},
                        "id": msg_id,
                    }
                    await websocket.send(json.dumps(sub_resp, default=str))
                    continue

                if method == "navig.bridge.stats" and msg_id is not None:
                    stats_resp = {
                        "jsonrpc": "2.0",
                        "result": event_bridge.get_stats(),
                        "id": msg_id,
                    }
                    await websocket.send(json.dumps(stats_resp, default=str))
                    continue

                if response:
                    await websocket.send(json.dumps(response, default=str))
        except websockets.exceptions.ConnectionClosed:
            pass  # connection closed normally
        finally:
            authenticated_clients.discard(client_id)
            authenticated_websockets.discard(websocket)
            event_bridge.unregister_client(websocket)

    async def _serve():
        import contextlib

        ch.success(f"NAVIG MCP WebSocket server listening on ws://{host}:{port}")
        ch.info(f"Session token: {session_token}")
        ch.dim("Clients must authenticate with the token before sending requests.")
        ch.dim("Press Ctrl+C to stop.")

        notifier_task = asyncio.create_task(_notification_loop())
        async with websockets.asyncio.server.serve(
            _handle_client,
            host,
            port,
            ping_interval=30,
            ping_timeout=10,
        ):
            try:
                await asyncio.Future()  # run forever
            finally:
                notifier_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await notifier_task

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        ch.info("MCP WebSocket server stopped.")


def _stdio_mcp_config() -> dict[str, Any]:
    """Shared stdio MCP configuration (used by VS Code and Claude Desktop)."""
    import sys

    return {
        "mcpServers": {
            "navig": {
                "command": sys.executable,
                "args": ["-m", "navig.mcp_server"],
                "env": {},
            }
        }
    }


def generate_vscode_mcp_config() -> dict[str, Any]:
    """Generate VS Code MCP configuration for Copilot integration."""
    return _stdio_mcp_config()


def generate_claude_mcp_config() -> dict[str, Any]:
    """Generate Claude Desktop MCP configuration."""
    return _stdio_mcp_config()


def generate_perplexity_mcp_config(
    host: str = "127.0.0.1", port: int = 3001, token: str | None = None
) -> dict[str, Any]:
    """Generate Perplexity AI custom-connector configuration.

    Copy the returned ``mcp_server_url`` and paste it into:
    Perplexity → Settings → Add custom connector → MCP Server URL
    """
    url = f"http://{host}:{port}/mcp"
    cfg: dict[str, Any] = {
        "name": "NAVIG",
        "description": "NAVIG CLI — server ops, database queries, wiki, and agent control",
        "mcp_server_url": url,
    }
    if token:
        cfg["authorization"] = f"Bearer {token}"
    return cfg


# =============================================================================
# Memory MCP Tool Handlers (module-level, importable for testing)
# =============================================================================

from navig.platform.paths import config_dir


def _memory_store():
    """Return a fresh KeyFactStore backed by the canonical DB path.

    Resolves the path **per call**. It previously used the deprecated
    ``KEY_FACTS_DB_PATH`` constant, which ``navig/memory/paths.py`` freezes at
    IMPORT time — so every MCP memory tool bound whatever ``NAVIG_HOME`` /
    ``NAVIG_CONFIG_DIR`` happened to be set when the module was first imported.
    Imports run at module load while the env is configured at CLI/daemon startup,
    so MCP could silently read and write a DIFFERENT key-facts database than the
    rest of NAVIG. ``get_key_facts_db_path()`` is the lazy, override-respecting
    resolver the module tells new code to use.
    """
    from navig.memory.key_facts import KeyFactStore
    from navig.memory.paths import get_key_facts_db_path

    return KeyFactStore(db_path=get_key_facts_db_path())


async def memory_retrieve(query: str, limit: int = 10, token_budget: int = 2000) -> dict:
    """Retrieve ranked key facts matching query within token budget.

    ``FactRetriever.retrieve(query, category=None, max_tokens=None, config_override=None)``
    returns a ``FactRetrievalResult``, not a list. This used to call it with
    ``limit=`` and ``token_budget=`` — neither parameter exists — and then iterate the
    result object, which is a dataclass and not iterable. Both raise TypeError, so the
    tool was dead on arrival; the facts live on ``result.facts`` as ``RankedFact``
    wrappers. ``navig/mcp/tools/memory.py`` has always done this correctly.
    """
    from navig.memory.fact_retriever import FactRetriever

    retriever = FactRetriever(_memory_store())
    result = retriever.retrieve(query, max_tokens=token_budget)
    ranked = (getattr(result, "facts", None) or [])[:limit]
    return {
        "facts": [
            {
                "id": rf.fact.id,
                "content": rf.fact.content,
                "category": rf.fact.category,
                "tags": rf.fact.tags,
                "confidence": rf.fact.confidence,
                "score": rf.combined_score,
                "created_at": rf.fact.created_at,
            }
            for rf in ranked
        ]
    }


async def memory_remember(text: str, source: str = "mcp") -> dict:
    """Extract and persist key facts from text."""
    from navig.memory.controls import memory_off_reason
    from navig.memory.fact_extractor import FactExtractor

    if reason := memory_off_reason():
        return {"added": 0, "error": reason}
    store = _memory_store()
    extractor = FactExtractor(store)
    added = await extractor.extract_and_store(
        user_text=text, assistant_text="", source_platform=source
    )
    return {"added": added}


async def memory_forget(fact_id: str) -> dict:
    """Soft-delete a key fact by ID."""
    store = _memory_store()
    ok = store.soft_delete(fact_id)
    return {"deleted": ok, "id": fact_id}


async def memory_stats() -> dict:
    """Return key fact store statistics."""
    store = _memory_store()
    return store.stats()


# Allow running as module: python -m navig.mcp_server
# Usage:
#   python -m navig.mcp_server                  # stdio mode, compact surface
#   python -m navig.mcp_server --profile full   # complete legacy catalogue
#   python -m navig.mcp_server --websocket      # WebSocket on port 3001
#   python -m navig.mcp_server --http --token T # HTTP with Bearer auth
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NAVIG MCP Server")
    parser.add_argument("--websocket", action="store_true", help="Run in WebSocket mode")
    parser.add_argument("--http", action="store_true", help="Run in HTTP (Streamable HTTP) mode")
    parser.add_argument(
        "--port", type=int, default=3001, help="Port for WebSocket/HTTP mode (default 3001)"
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1", help="Bind host (default 127.0.0.1)"
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Auth token (auto-generated if omitted for websocket)",
    )
    parser.add_argument(
        "--profile",
        choices=("default", "full"),
        default="default",
        help="default = compact routed surface; full = complete catalogue",
    )
    parser.add_argument(
        "--exposure",
        choices=("local", "remote"),
        default=None,
        help="Declare this stdio server remote when a proxy exposes it",
    )
    parser.add_argument(
        "--insecure-local",
        action="store_true",
        help="Keep the full surface unauthenticated on a loopback bind (explicit risk)",
    )
    args = parser.parse_args()

    if args.http:
        mode = "http"
    elif args.websocket or args.port != 3001:
        mode = "websocket"
    else:
        mode = "stdio"
    start_mcp_server(
        mode=mode,
        port=args.port,
        token=args.token,
        host=args.host,
        profile=args.profile,
        exposure=args.exposure,
        insecure_local=args.insecure_local,
    )
