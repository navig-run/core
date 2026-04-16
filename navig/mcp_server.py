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
import json
import logging
import secrets
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

from navig import console_helper as ch
from navig.config import ConfigManager

logger = logging.getLogger(__name__)


class MCPProtocolHandler:
    """Handles MCP JSON-RPC protocol over stdio."""

    def __init__(self):
        self.tools: dict[str, dict[str, Any]] = {}
        self.resources: dict[str, dict[str, Any]] = {}
        self.prompts: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable] = {}
        self._running = False
        self._config = ConfigManager()

        # Register built-in methods
        self._setup_protocol_handlers()
        self._setup_navig_tools()
        self._setup_navig_resources()

    def _setup_protocol_handlers(self):
        """Setup MCP protocol method handlers."""
        self._handlers = {}
        self._handlers["initialize"] = self._handle_initialize
        self._handlers["initialized"] = self._handle_initialized
        self._handlers["tools/list"] = self._handle_tools_list
        self._handlers["tools/call"] = self._handle_tools_call
        self._handlers["resources/list"] = self._handle_resources_list
        self._handlers["resources/read"] = self._handle_resources_read
        self._handlers["prompts/list"] = self._handle_prompts_list
        self._handlers["prompts/get"] = self._handle_prompts_get
        self._handlers["ping"] = self._handle_ping

    def _setup_navig_tools(self):
        """Register NAVIG tools for MCP."""
        self.tools = {}
        self._tool_handlers = {}
        from navig.mcp.tools import register_all_tools

        register_all_tools(self)

    def _setup_navig_resources(self):
        """Register NAVIG resources for MCP."""
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
        """Handle MCP initialize request."""
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "navig-mcp-server", "version": "1.0.0"},
        }

    def _handle_initialized(self, params: dict[str, Any]) -> None:
        """Handle MCP initialized notification."""
        return None

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ping request."""
        return {}

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return list of available tools."""
        return {"tools": list(self.tools.values())}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in self.tools:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
            }

        try:
            result = self._execute_tool(tool_name, arguments)
            return {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]
            }
        except Exception as e:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Tool error: {str(e)}"}],
            }

    def _handle_resources_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return list of available resources."""
        return {"resources": list(self.resources.values())}

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
        if handler:
            return handler(self, arguments)
        raise ValueError(f"Unknown tool: {tool_name}")

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

    def run_stdio(self):
        """Run MCP server in stdio mode."""
        self._running = True

        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                message = json.loads(line)
                response = self.handle_message(message)

                if response:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()

            except json.JSONDecodeError as e:
                sys.stderr.write(f"JSON decode error: {e}\n")
            except Exception as e:
                sys.stderr.write(f"Error: {e}\n")

    def stop(self):
        """Stop the server."""
        self._running = False


def start_mcp_server(
    mode: str = "stdio",
    port: int = 3001,
    token: str | None = None,
    host: str = "127.0.0.1",
):
    """Start the NAVIG MCP server.

    Args:
        mode: Server mode — 'stdio', 'websocket', or 'http'
        port: Port for WebSocket/HTTP mode (default 3001)
        token: Optional auth token; auto-generated if None in websocket mode
        host: Bind address for HTTP/WebSocket modes (default 127.0.0.1)
    """
    handler = MCPProtocolHandler()

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

    _CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept",
    }

    def _check_auth(request: web.Request) -> bool:
        if not session_token:
            return True
        auth = request.headers.get("Authorization", "")
        return auth == f"Bearer {session_token}"

    async def handle_options(request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={**_CORS_HEADERS, "Access-Control-Max-Age": "86400"},
        )

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

from navig.memory.paths import KEY_FACTS_DB_PATH as _KEY_FACTS_DB_PATH
from navig.platform.paths import config_dir


def _memory_store():
    """Return a fresh KeyFactStore backed by the canonical DB path."""
    from navig.memory.key_facts import KeyFactStore

    return KeyFactStore(db_path=_KEY_FACTS_DB_PATH)


async def memory_retrieve(query: str, limit: int = 10, token_budget: int = 2000) -> dict:
    """Retrieve ranked key facts matching query within token budget."""
    from navig.memory.fact_retriever import FactRetriever

    retriever = FactRetriever(_memory_store())
    facts = retriever.retrieve(query=query, limit=limit, token_budget=token_budget)
    return {"facts": [f.model_dump() if hasattr(f, "model_dump") else vars(f) for f in facts]}


async def memory_remember(text: str, source: str = "mcp") -> dict:
    """Extract and persist key facts from text."""
    from navig.memory.fact_extractor import FactExtractor

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
#   python -m navig.mcp_server                  # stdio mode
#   python -m navig.mcp_server --websocket      # WebSocket on port 3001
#   python -m navig.mcp_server --port 3001      # Same as above
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
    args = parser.parse_args()

    if args.http:
        mode = "http"
    elif args.websocket or args.port != 3001:
        mode = "websocket"
    else:
        mode = "stdio"
    start_mcp_server(mode=mode, port=args.port, token=args.token, host=args.host)
