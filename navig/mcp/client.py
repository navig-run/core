"""MCP Client — connects to a single external MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navig.debug_logger import get_debug_logger

from .protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    MCPCapabilities,
    MCPMethod,
    MCPPrompt,
    MCPResource,
    MCPTool,
)
from .transport import MCPTransport, SSETransport, StdioTransport, WebSocketTransport

logger = get_debug_logger()


@dataclass
class MCPClientConfig:
    """Configuration for a single MCP client connection."""

    id: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # "stdio" | "sse" | "websocket"
    url: str | None = None
    cwd: str | None = None
    auto_connect: bool = True
    enabled: bool = True

    @classmethod
    def from_dict(cls, id: str, data: dict[str, Any]) -> MCPClientConfig:
        return cls(
            id=id,
            command=data.get("command"),
            args=data.get("args", []),
            env=data.get("env", {}),
            transport=data.get("transport", "stdio"),
            url=data.get("url"),
            cwd=data.get("cwd"),
            auto_connect=data.get("auto_connect", True),
            enabled=data.get("enabled", True),
        )


class MCPClient:
    """MCP client that manages the lifecycle of one server connection.

    Handles:
    - Transport selection and connection lifecycle.
    - MCP protocol initialisation handshake.
    - Tool, resource, and prompt discovery.
    - Request routing via ``call_tool`` / ``read_resource`` / ``get_prompt``.

    Example::

        config = MCPClientConfig(
            id="fs",
            command="npx",
            args=["-y", "@anthropic/mcp-server-filesystem", "/tmp"],
        )
        client = MCPClient(config)
        await client.connect()

        for tool in client.tools:
            print(tool.name, tool.description)

        result = await client.call_tool("read_file", {"path": "/tmp/hello.txt"})
        await client.disconnect()
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, config: MCPClientConfig) -> None:
        self.config = config
        self.id = config.id

        self._transport: MCPTransport | None = None
        self._request_id = 0
        self._tools: dict[str, MCPTool] = {}
        self._resources: dict[str, MCPResource] = {}
        self._prompts: dict[str, MCPPrompt] = {}
        self._capabilities: MCPCapabilities | None = None
        self._initialized = False
        self._server_info: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """``True`` when the transport is up **and** the MCP handshake is complete."""
        return (
            self._transport is not None
            and self._transport.is_connected()
            and self._initialized
        )

    @property
    def tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    @property
    def resources(self) -> list[MCPResource]:
        return list(self._resources.values())

    @property
    def prompts(self) -> list[MCPPrompt]:
        return list(self._prompts.values())

    @property
    def capabilities(self) -> MCPCapabilities | None:
        return self._capabilities

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the MCP server and complete the initialisation handshake."""
        if self.is_connected:
            logger.warning("MCP client %s is already connected", self.id)
            return

        transport = self._build_transport()
        await transport.connect()
        self._transport = transport

        try:
            await self._initialize()

            if self._capabilities:
                if self._capabilities.tools:
                    await self._discover_tools()
                if self._capabilities.resources:
                    await self._discover_resources()
                if self._capabilities.prompts:
                    await self._discover_prompts()

            self._initialized = True
            logger.info(
                "MCP client %s connected: %d tools, %d resources, %d prompts",
                self.id,
                len(self._tools),
                len(self._resources),
                len(self._prompts),
            )
        except Exception as exc:
            await transport.disconnect()
            self._transport = None
            raise RuntimeError(f"MCP initialisation failed for {self.id!r}: {exc}") from exc

    async def disconnect(self) -> None:
        """Disconnect from the MCP server and clear all cached state."""
        if self._transport is not None:
            await self._transport.disconnect()
            self._transport = None

        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()
        self._capabilities = None
        self._initialized = False
        self._server_info = {}

        logger.info("MCP client %s disconnected", self.id)

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """Call a tool on the connected server.

        Args:
            name:      Tool name (must be listed in :attr:`tools`).
            arguments: Arguments dict; ``None`` is normalised to ``{}``.

        Returns:
            Tool result, unwrapped from the MCP content envelope when possible.

        Raises:
            RuntimeError: When not connected or the server returns an error.
            ValueError:   When the tool name is not found.
        """
        self._assert_connected()

        if name not in self._tools:
            raise ValueError(
                f"Tool {name!r} not found on client {self.id!r} "
                f"(available: {list(self._tools)})"
            )

        response = await self._send_request(
            MCPMethod.TOOLS_CALL, {"name": name, "arguments": arguments or {}}
        )
        if response.is_error:
            raise RuntimeError(
                f"Tool call {name!r} failed: {response.get_error_message()}"
            )

        result = response.result
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            # Unwrap single-item text content for ergonomic calling.
            if (
                isinstance(content, list)
                and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
            ):
                return content[0].get("text", "")
            return content

        return result

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the connected server."""
        self._assert_connected()
        response = await self._send_request(MCPMethod.RESOURCES_READ, {"uri": uri})
        if response.is_error:
            raise RuntimeError(
                f"Resource read {uri!r} failed: {response.get_error_message()}"
            )
        return response.result

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> Any:
        """Retrieve a prompt from the connected server."""
        self._assert_connected()
        response = await self._send_request(
            MCPMethod.PROMPTS_GET, {"name": name, "arguments": arguments or {}}
        )
        if response.is_error:
            raise RuntimeError(
                f"Prompt get {name!r} failed: {response.get_error_message()}"
            )
        return response.result

    async def ping(self) -> bool:
        """Return ``True`` if the server responds to a ping."""
        if not self._transport or not self._transport.is_connected():
            return False
        try:
            response = await self._send_request(MCPMethod.PING, {})
            return not response.is_error
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_transport(self) -> MCPTransport:
        """Construct the appropriate transport from config."""
        transport_type = self.config.transport

        if transport_type == "sse":
            if not self.config.url:
                raise ValueError(
                    f"SSE transport requires 'url' for client {self.id!r}"
                )
            return SSETransport(self.config.url)

        if transport_type == "websocket":
            if not self.config.url:
                raise ValueError(
                    f"WebSocket transport requires 'url' for client {self.id!r}"
                )
            return WebSocketTransport(self.config.url)

        # Default: stdio
        if not self.config.command:
            raise ValueError(
                f"Stdio transport requires 'command' for client {self.id!r}"
            )
        return StdioTransport(
            command=self.config.command,
            args=self.config.args,
            env=self.config.env,
            cwd=self.config.cwd,
        )

    def _assert_connected(self) -> None:
        if not self.is_connected:
            raise RuntimeError(f"MCP client {self.id!r} is not connected")

    async def _initialize(self) -> None:
        """Execute the MCP initialise handshake."""
        response = await self._send_request(
            MCPMethod.INITIALIZE,
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": True}},
                "clientInfo": {"name": "navig", "version": "1.0.0"},
            },
        )
        if response.is_error:
            raise RuntimeError(
                f"MCP initialise failed: {response.get_error_message()}"
            )

        result = response.result or {}
        self._server_info = result.get("serverInfo", {})
        self._capabilities = MCPCapabilities.from_dict(result)

        logger.debug(
            "MCP server connected: %s", self._server_info.get("name", "unknown")
        )

        await self._send_notification(MCPMethod.INITIALIZED, {})

    async def _discover_tools(self) -> None:
        response = await self._send_request(MCPMethod.TOOLS_LIST, {})
        if response.is_error:
            logger.warning("Failed to list tools: %s", response.get_error_message())
            return
        for tool_data in (response.result or {}).get("tools", []):
            tool = MCPTool.from_dict(tool_data, server_id=self.id)
            self._tools[tool.name] = tool

    async def _discover_resources(self) -> None:
        response = await self._send_request(MCPMethod.RESOURCES_LIST, {})
        if response.is_error:
            logger.warning(
                "Failed to list resources: %s", response.get_error_message()
            )
            return
        for res_data in (response.result or {}).get("resources", []):
            resource = MCPResource.from_dict(res_data, server_id=self.id)
            self._resources[resource.uri] = resource

    async def _discover_prompts(self) -> None:
        response = await self._send_request(MCPMethod.PROMPTS_LIST, {})
        if response.is_error:
            logger.warning(
                "Failed to list prompts: %s", response.get_error_message()
            )
            return
        for prompt_data in (response.result or {}).get("prompts", []):
            prompt = MCPPrompt(
                name=prompt_data["name"],
                description=prompt_data.get("description"),
                arguments=prompt_data.get("arguments", []),
                server_id=self.id,
            )
            self._prompts[prompt.name] = prompt

    async def _send_request(
        self, method: MCPMethod, params: dict[str, Any]
    ) -> JSONRPCResponse:
        self._request_id += 1
        request = JSONRPCRequest(
            method=method.value, params=params, id=self._request_id
        )
        assert self._transport is not None  # guarded by _assert_connected callers
        response_data = await self._transport.send(request.to_json())
        return JSONRPCResponse.from_json(response_data)

    async def _send_notification(
        self, method: MCPMethod, params: dict[str, Any]
    ) -> None:
        request = JSONRPCRequest(
            method=method.value, params=params, id=None
        )
        assert self._transport is not None
        await self._transport.send_notification(request.to_json())
