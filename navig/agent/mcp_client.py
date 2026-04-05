"""
navig.agent.mcp_client — MCP client for consuming external tool servers.

Connects to MCP servers via **stdio** (subprocess pipe) or **HTTP**
(StreamableHTTP) and exposes their tools as :class:`AgentToolEntry` items in
the :data:`~navig.agent.agent_tool_registry._AGENT_REGISTRY`.

Protocol: JSON-RPC 2.0 — ``initialize`` → ``tools/list`` → ``tools/call``.

Usage::

    from navig.agent.mcp_client import MCPClientPool

    pool = MCPClientPool()
    await pool.connect_all()             # reads config.agent.mcp.servers
    # tools are now registered in _AGENT_REGISTRY under "mcp:<server>"
    await pool.close_all()

Config shape (in navig config.yaml)::

    agent:
      mcp:
        servers:
          - name: filesystem
            transport: stdio
            command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
          - name: remote_api
            transport: http
            url: "http://localhost:8080/mcp"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

_PROTOCOL_VERSION = "2024-11-05"
_MAX_RECONNECT_ATTEMPTS = 5
_RECONNECT_BASE_DELAY = 1.0  # seconds
_RECONNECT_MAX_DELAY = 60.0
_RPC_TIMEOUT = 30.0  # seconds per request


# ─────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────

@dataclass
class MCPServerConfig:
    """Configuration for a single external MCP server."""

    name: str
    transport: str = "stdio"  # "stdio" | "http"
    command: list[str] = field(default_factory=list)  # for stdio
    url: str = ""  # for http
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class MCPToolSpec:
    """A tool discovered from an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


# ─────────────────────────────────────────────────────────────
# Transport: JSON-RPC 2.0 over stdio
# ─────────────────────────────────────────────────────────────

class _StdioTransport:
    """Manage a subprocess speaking JSON-RPC 2.0 over stdin/stdout."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None):
        self._command = command
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Launch the subprocess."""
        safe_env = _build_safe_env(self._env)
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop(self) -> None:
        """Terminate the subprocess."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
        # Fail any pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("MCP transport closed"))
        self._pending.clear()

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for the response."""
        if not self.is_alive:
            raise ConnectionError("MCP stdio transport is not running")

        async with self._lock:
            self._request_id += 1
            req_id = self._request_id

        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        assert self._process and self._process.stdin  # noqa: S101
        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

        try:
            return await asyncio.wait_for(future, timeout=_RPC_TIMEOUT)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request {method!r} timed out after {_RPC_TIMEOUT}s") from exc

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.is_alive:
            return
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        assert self._process and self._process.stdin  # noqa: S101
        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    async def _reader_loop(self) -> None:
        """Read JSON-RPC responses from stdout and dispatch to pending futures."""
        assert self._process and self._process.stdout  # noqa: S101
        try:
            while True:
                raw = await self._process.stdout.readline()
                if not raw:
                    break  # EOF
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("MCP: non-JSON line from server: %s", line[:200])
                    continue

                # Dispatch response
                if "id" in data and data["id"] in self._pending:
                    fut = self._pending.pop(data["id"])
                    if "error" in data:
                        fut.set_exception(
                            RuntimeError(
                                f"MCP error {data['error'].get('code')}: "
                                f"{data['error'].get('message', 'unknown')}"
                            )
                        )
                    else:
                        fut.set_result(data.get("result", {}))
                elif "method" in data:
                    # Server-initiated notification
                    self._handle_notification(data)
        except asyncio.CancelledError:
            pass  # expected during task cancellation
        except Exception as exc:
            logger.debug("MCP reader loop error: %s", exc)

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        """Handle server-initiated notifications."""
        method = msg.get("method", "")
        if method == "notifications/tools/list_changed":
            logger.info("MCP server reports tool list changed")
            # The MCPClient will handle re-fetching tools
        else:
            logger.debug("MCP notification: %s", method)


# ─────────────────────────────────────────────────────────────
# Transport: JSON-RPC 2.0 over HTTP (StreamableHTTP)
# ─────────────────────────────────────────────────────────────

class _HttpTransport:
    """Send JSON-RPC 2.0 over HTTP POST.

    Simplified HTTP transport — each request is an independent POST.
    """

    def __init__(self, url: str):
        self._url = url
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._session_id: str | None = None

    @property
    def is_alive(self) -> bool:
        return True  # stateless HTTP is always "alive"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self._session_id = None

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            self._request_id += 1
            req_id = self._request_id

        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        try:
            import aiohttp

            headers = {"Content-Type": "application/json"}
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url, json=msg, headers=headers, timeout=aiohttp.ClientTimeout(total=_RPC_TIMEOUT)
                ) as resp:
                    data = await resp.json()
                    # Capture session ID from response headers
                    if "Mcp-Session-Id" in resp.headers:
                        self._session_id = resp.headers["Mcp-Session-Id"]
                    if "error" in data:
                        raise RuntimeError(
                            f"MCP error {data['error'].get('code')}: "
                            f"{data['error'].get('message', 'unknown')}"
                        )
                    return data.get("result", {})
        except ImportError:
            # Fallback: urllib (sync, no aiohttp)
            import urllib.request

            body = json.dumps(msg).encode("utf-8")
            req = urllib.request.Request(
                self._url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_RPC_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
                if "error" in data:
                    raise RuntimeError(
                        f"MCP error {data['error'].get('code')}: "
                        f"{data['error'].get('message', 'unknown')}"
                    ) from None
                return data.get("result", {})

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        """Notifications over HTTP are fire-and-forget POST."""
        try:
            await self.send_request(method, params)
        except Exception:
            pass  # best-effort fire-and-forget; notification failure is non-critical


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _build_safe_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build env for subprocess, stripping sensitive ``NAVIG_`` vars."""
    env = dict(os.environ)
    # Strip credential-bearing NAVIG_ vars
    for key in list(env):
        if key.startswith("NAVIG_") and any(
            s in key.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "KEY", "CREDENTIAL")
        ):
            del env[key]
    if extra:
        env.update(extra)
    return env


# ─────────────────────────────────────────────────────────────
# MCPClient — single server connection
# ─────────────────────────────────────────────────────────────

class MCPClient:
    """Client connection to a single external MCP server.

    Implements the MCP 2024-11-05 client protocol: ``initialize`` →
    ``tools/list`` → repeated ``tools/call``.

    Args:
        config: Server connection configuration.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._transport: _StdioTransport | _HttpTransport | None = None
        self._tools: list[MCPToolSpec] = []
        self._connected = False
        self._reconnect_count = 0

    @property
    def tools(self) -> list[MCPToolSpec]:
        """Discovered tools from this server."""
        return list(self._tools)

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport is not None and self._transport.is_alive

    # ── Connection lifecycle ──

    async def connect(self) -> None:
        """Start transport, do MCP handshake, discover tools."""
        if self.config.transport == "stdio":
            if not self.config.command:
                raise ValueError(f"MCP server {self.config.name!r}: 'command' required for stdio transport")
            self._transport = _StdioTransport(self.config.command, self.config.env)
        elif self.config.transport == "http":
            if not self.config.url:
                raise ValueError(f"MCP server {self.config.name!r}: 'url' required for http transport")
            self._transport = _HttpTransport(self.config.url)
        else:
            raise ValueError(f"Unknown MCP transport: {self.config.transport!r}")

        await self._transport.start()

        # MCP handshake
        try:
            result = await self._transport.send_request(
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "navig", "version": "1.0.0"},
                },
            )
            server_info = result.get("serverInfo", {})
            logger.info(
                "MCP server %r connected: %s v%s",
                self.config.name,
                server_info.get("name", "?"),
                server_info.get("version", "?"),
            )

            # Send initialized notification
            await self._transport.send_notification("notifications/initialized")

        except Exception as exc:
            logger.error("MCP handshake failed for %r: %s", self.config.name, exc)
            await self.disconnect()
            raise

        self._connected = True

        # Discover tools
        await self.refresh_tools()

    async def disconnect(self) -> None:
        """Close the transport."""
        self._connected = False
        if self._transport:
            await self._transport.stop()
        self._tools.clear()

    async def reconnect(self) -> bool:
        """Attempt reconnection with exponential backoff.

        Returns:
            ``True`` if reconnection succeeded, ``False`` if all attempts failed.
        """
        for attempt in range(1, _MAX_RECONNECT_ATTEMPTS + 1):
            delay = min(
                _RECONNECT_BASE_DELAY * (2 ** (attempt - 1)),
                _RECONNECT_MAX_DELAY,
            )
            logger.debug(
                "MCP reconnect attempt %d/%d for %r in %.1fs",
                attempt,
                _MAX_RECONNECT_ATTEMPTS,
                self.config.name,
                delay,
            )
            await asyncio.sleep(delay)
            try:
                await self.disconnect()
                await self.connect()
                self._reconnect_count += 1
                return True
            except Exception as exc:
                logger.debug("MCP reconnect attempt %d failed: %s", attempt, exc)
        logger.error(
            "MCP server %r: all %d reconnect attempts failed",
            self.config.name,
            _MAX_RECONNECT_ATTEMPTS,
        )
        return False

    # ── Tool discovery ──

    async def refresh_tools(self) -> list[MCPToolSpec]:
        """Fetch the current tool list from the server."""
        if not self._transport or not self._transport.is_alive:
            return []

        try:
            result = await self._transport.send_request("tools/list")
        except Exception as exc:
            logger.warning("MCP tools/list failed for %r: %s", self.config.name, exc)
            return []

        raw_tools = result.get("tools", [])
        self._tools = [
            MCPToolSpec(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {"type": "object", "properties": {}}),
                server_name=self.config.name,
            )
            for t in raw_tools
            if t.get("name")
        ]
        logger.info(
            "MCP server %r: discovered %d tools: %s",
            self.config.name,
            len(self._tools),
            [t.name for t in self._tools],
        )
        return list(self._tools)

    # ── Tool invocation ──

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call a tool on the MCP server.

        Args:
            name:      Tool name.
            arguments: Tool arguments.

        Returns:
            Tool result as a string.

        Raises:
            ConnectionError: If not connected.
            RuntimeError: If the server returns an error.
        """
        if not self.is_connected:
            # Try reconnect once
            if not await self.reconnect():
                raise ConnectionError(f"MCP server {self.config.name!r} is not connected")

        try:
            result = await self._transport.send_request(  # type: ignore[union-attr]
                "tools/call",
                {"name": name, "arguments": arguments or {}},
            )
        except ConnectionError:
            # Transport died — try reconnect
            if await self.reconnect():
                result = await self._transport.send_request(  # type: ignore[union-attr]
                    "tools/call",
                    {"name": name, "arguments": arguments or {}},
                )
            else:
                raise

        # Parse MCP content array → string
        content_items = result.get("content", [])
        is_error = result.get("isError", False)

        parts: list[str] = []
        for item in content_items:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    parts.append(f"[image: {item.get('mimeType', 'unknown')}]")
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))

        from navig.agent.tool_caps import cap_result
        output = "\n".join(parts)
        output = cap_result(output, tool_name=f"mcp:{name}" if name else "mcp")

        if is_error:
            return f"[MCP error] {output}"
        return output

    # ── Resource discovery (PlanContext integration) ──

    async def list_resources(self) -> list[dict[str, str]]:
        """Fetch MCP resources from the server via ``resources/list``.

        Returns a list of dicts with ``uri``, ``name``, and ``description``.
        Empty list if the server does not support resources.
        """
        if not self.is_connected:
            return []
        try:
            result = await self._transport.send_request("resources/list")  # type: ignore[union-attr]
            raw = result.get("resources", [])
            return [
                {
                    "uri": r.get("uri", ""),
                    "name": r.get("name", ""),
                    "description": r.get("description", ""),
                }
                for r in raw[:20]
                if isinstance(r, dict)
            ]
        except Exception as exc:
            logger.debug("MCP resources/list failed for %r: %s", self.config.name, exc)
            return []


# ─────────────────────────────────────────────────────────────
# MCPClientPool — manages multiple server connections
# ─────────────────────────────────────────────────────────────

class MCPClientPool:
    """Pool of :class:`MCPClient` connections, one per configured MCP server.

    Reads ``config.agent.mcp.servers`` to find server definitions and
    auto-registers discovered tools into :data:`_AGENT_REGISTRY`.
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}

    @property
    def clients(self) -> dict[str, MCPClient]:
        return dict(self._clients)

    # ── Config loading ──

    def _load_server_configs(self) -> list[MCPServerConfig]:
        """Read MCP server configs from navig config."""
        configs: list[MCPServerConfig] = []
        try:
            from navig.config import config

            agent_cfg = getattr(config, "agent", None)
            if agent_cfg is None:
                return configs
            mcp_cfg = (
                agent_cfg.get("mcp", {})
                if hasattr(agent_cfg, "get")
                else getattr(agent_cfg, "mcp", {}) or {}
            )
            servers = (
                mcp_cfg.get("servers", [])
                if isinstance(mcp_cfg, dict)
                else getattr(mcp_cfg, "servers", []) or []
            )
            for srv in servers:
                if isinstance(srv, dict):
                    configs.append(
                        MCPServerConfig(
                            name=srv.get("name", "unnamed"),
                            transport=srv.get("transport", "stdio"),
                            command=srv.get("command", []),
                            url=srv.get("url", ""),
                            env=srv.get("env", {}),
                            enabled=srv.get("enabled", True),
                        )
                    )
        except Exception as exc:
            logger.debug("Could not load MCP server configs: %s", exc)

        # Also check NAVIG_MCP_SERVERS env var (JSON list)
        env_servers = os.environ.get("NAVIG_MCP_SERVERS")
        if env_servers:
            try:
                for srv in json.loads(env_servers):
                    configs.append(
                        MCPServerConfig(
                            name=srv.get("name", "env_unnamed"),
                            transport=srv.get("transport", "stdio"),
                            command=srv.get("command", []),
                            url=srv.get("url", ""),
                            env=srv.get("env", {}),
                            enabled=srv.get("enabled", True),
                        )
                    )
            except Exception as exc:
                logger.debug("Bad NAVIG_MCP_SERVERS env: %s", exc)

        return configs

    # ── Lifecycle ──

    async def connect_all(self) -> dict[str, bool]:
        """Connect to all configured MCP servers.

        Returns:
            Dict of server_name → success.
        """
        configs = self._load_server_configs()
        results: dict[str, bool] = {}
        for cfg in configs:
            if not cfg.enabled:
                logger.debug("MCP server %r disabled, skipping", cfg.name)
                results[cfg.name] = False
                continue
            client = MCPClient(cfg)
            try:
                await client.connect()
                self._clients[cfg.name] = client
                self._register_tools(client)
                results[cfg.name] = True
            except Exception as exc:
                logger.warning("MCP server %r failed to connect: %s", cfg.name, exc)
                results[cfg.name] = False
        return results

    async def close_all(self) -> None:
        """Disconnect all MCP clients and deregister their tools."""
        for name, client in list(self._clients.items()):
            self._deregister_tools(client)
            try:
                await client.disconnect()
            except Exception as exc:
                logger.debug("Error closing MCP client %r: %s", name, exc)
        self._clients.clear()

    async def refresh_tools(self, server_name: str | None = None) -> None:
        """Re-fetch tool lists from connected servers."""
        targets = (
            [self._clients[server_name]]
            if server_name and server_name in self._clients
            else list(self._clients.values())
        )
        for client in targets:
            self._deregister_tools(client)
            await client.refresh_tools()
            self._register_tools(client)

    # ── Registry integration ──

    def _register_tools(self, client: MCPClient) -> None:
        """Register discovered MCP tools into the AgentToolRegistry."""
        try:
            from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        except ImportError:
            logger.debug("AgentToolRegistry not available — MCP tools not registered")
            return

        toolset_name = f"mcp:{client.config.name}"

        for spec in client.tools:
            # Create a wrapper BaseTool-like object
            wrapper = _MCPToolWrapper(
                tool_name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                client=client,
            )
            try:
                _AGENT_REGISTRY.register(
                    tool=wrapper,
                    toolset=toolset_name,
                )
                logger.debug("Registered MCP tool %r in toolset %r", spec.name, toolset_name)
            except Exception as exc:
                logger.debug("Failed to register MCP tool %r: %s", spec.name, exc)

    def _deregister_tools(self, client: MCPClient) -> None:
        """Remove a client's tools from the registry."""
        try:
            from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        except ImportError:
            return

        for spec in client.tools:
            try:
                _AGENT_REGISTRY.deregister(spec.name)
            except KeyError:
                pass  # best-effort: key absent; skip
    # ── Resource listing (PlanContext integration) ──

    async def list_resources(self, timeout: float = 2.0) -> list[dict[str, str]]:
        """Collect MCP resources from all connected servers.

        Args:
            timeout: Per-server timeout in seconds.

        Returns:
            Flattened list of resource dicts (max 10).
        """
        all_resources: list[dict[str, str]] = []
        for client in self._clients.values():
            try:
                res = await asyncio.wait_for(client.list_resources(), timeout=timeout)
                all_resources.extend(res)
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                logger.debug("list_resources timeout/error for %r: %s", client.config.name, exc)
        return all_resources[:10]

    def list_resources_sync(self, timeout: float = 2.0) -> list[dict[str, str]]:
        """Synchronous wrapper for :meth:`list_resources`.

        Creates an event loop if none is running (safe for sync callers).
        Returns empty list on any failure.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside an async context — can't block; return empty
            logger.debug("list_resources_sync: async loop running, returning empty")
            return []

        try:
            return asyncio.run(self.list_resources(timeout=timeout))
        except Exception as exc:  # noqa: BLE001
            logger.debug("list_resources_sync failed: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────
# MCP tool wrapper (adapts MCP tool spec to BaseTool interface)
# ─────────────────────────────────────────────────────────────

class _MCPToolWrapper:
    """Wraps an MCP server tool to look like a ``BaseTool`` for the registry.

    This is a lightweight adapter — the actual call goes through `MCPClient.call_tool()`.
    """

    def __init__(
        self,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        client: MCPClient,
    ) -> None:
        self.name: str = tool_name
        self.description: str = description
        self.parameters: dict[str, Any] = input_schema  # Already JSON Schema
        self._client = client

    async def run(self, args: dict[str, Any], **kwargs: Any) -> Any:
        """Call the MCP tool and return a ToolResult-compatible object."""
        output = await self._client.call_tool(self.name, args)
        # Return a simple namespace with .output and .error
        return _ToolResultCompat(output=output, error=None)


class _ToolResultCompat:
    """Minimal ToolResult-like object for MCP tool outputs."""

    def __init__(self, output: str, error: str | None) -> None:
        self.output = output
        self.error = error
        self.content = output  # alias


# ─────────────────────────────────────────────────────────────
# Module-level convenience
# ─────────────────────────────────────────────────────────────

_pool: MCPClientPool | None = None


def get_mcp_pool() -> MCPClientPool:
    """Return the module-level :class:`MCPClientPool` singleton."""
    global _pool
    if _pool is None:
        _pool = MCPClientPool()
    return _pool


async def connect_mcp_servers() -> dict[str, bool]:
    """Connect to all configured MCP servers and register tools.

    Convenience function that creates + uses the singleton pool.
    """
    pool = get_mcp_pool()
    return await pool.connect_all()


async def close_mcp_servers() -> None:
    """Close all MCP server connections."""
    global _pool
    if _pool is not None:
        await _pool.close_all()
        _pool = None
