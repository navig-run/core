"""MCP Client for connecting to external MCP servers."""

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
from .transport import MCPTransport, SSETransport, StdioTransport

logger = get_debug_logger()


@dataclass
class MCPClientConfig:
    """Configuration for an MCP client connection."""

    id: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # "stdio" or "sse"
    url: str | None = None  # For SSE transport
    cwd: str | None = None  # Working directory
    auto_connect: bool = True
    enabled: bool = True

    @classmethod
    def from_dict(cls, id: str, data: dict) -> "MCPClientConfig":
        """Create from config dictionary."""
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
    """
    MCP client for connecting to external MCP servers.

    Handles:
    - Connection lifecycle
    - Protocol initialization
    - Tool discovery and invocation
    - Resource access

    Example:
        config = MCPClientConfig(
            id="filesystem",
            command="npx",
            args=["-y", "@anthropic/mcp-server-filesystem", "/tmp"],
        )

        client = MCPClient(config)
        await client.connect()

        # List available tools
        for tool in client.tools:
            print(f"{tool.name}: {tool.description}")

        # Call a tool
        result = await client.call_tool("read_file", {"path": "/tmp/test.txt"})

        await client.disconnect()
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, config: MCPClientConfig):
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

    @property
    def is_connected(self) -> bool:
        """Check if client is connected and initialized."""
        return (
            self._transport is not None
            and self._transport.is_connected()
            and self._initialized
        )

    @property
    def tools(self) -> list[MCPTool]:
        """Get list of available tools."""
        return list(self._tools.values())

    @property
    def resources(self) -> list[MCPResource]:
        """Get list of available resources."""
        return list(self._resources.values())

    @property
    def prompts(self) -> list[MCPPrompt]:
        """Get list of available prompts."""
        return list(self._prompts.values())

    @property
    def capabilities(self) -> MCPCapabilities | None:
        """Get server capabilities."""
        return self._capabilities

    async def connect(self):
        """Connect to MCP server and initialize protocol."""
        if self.is_connected:
            logger.warning(f"MCP client {self.id} already connected")
            return

        # Create transport based on config
        if self.config.transport == "sse":
            if not self.config.url:
                raise ValueError(f"SSE transport requires 'url' for client {self.id}")
            self._transport = SSETransport(self.config.url)
        else:
            if not self.config.command:
                raise ValueError(
                    f"Stdio transport requires 'command' for client {self.id}"
                )
            self._transport = StdioTransport(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env,
                cwd=self.config.cwd,
            )

        await self._transport.connect()

        try:
            # Initialize protocol
            await self._initialize()

            # Discover capabilities
            if self._capabilities:
                if self._capabilities.tools:
                    await self._discover_tools()
                if self._capabilities.resources:
                    await self._discover_resources()
                if self._capabilities.prompts:
                    await self._discover_prompts()

            self._initialized = True
            logger.info(
                f"MCP client {self.id} connected: "
                f"{len(self._tools)} tools, "
                f"{len(self._resources)} resources, "
                f"{len(self._prompts)} prompts"
            )

        except Exception as e:
            await self._transport.disconnect()
            self._transport = None
            raise RuntimeError(f"MCP initialization failed: {e}") from e

    async def disconnect(self):
        """Disconnect from MCP server."""
        if self._transport:
            await self._transport.disconnect()
            self._transport = None

        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()
        self._capabilities = None
        self._initialized = False
        self._server_info = {}

        logger.info(f"MCP client {self.id} disconnected")

    async def call_tool(self, name: str, arguments: dict[str, Any] = None) -> Any:
        """
        Call a tool on the connected server.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool result (content)

        Raises:
            ValueError: If tool not found
            RuntimeError: If call fails
        """
        if not self.is_connected:
            raise RuntimeError(f"MCP client {self.id} not connected")

        if name not in self._tools:
            raise ValueError(
                f"Tool not found: {name} (available: {list(self._tools.keys())})"
            )

        response = await self._send_request(
            MCPMethod.TOOLS_CALL, {"name": name, "arguments": arguments or {}}
        )

        if response.is_error:
            raise RuntimeError(f"Tool call failed: {response.get_error_message()}")

        # Extract content from result
        result = response.result
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            # If single text content, return just the text
            if isinstance(content, list) and len(content) == 1:
                item = content[0]
                if item.get("type") == "text":
                    return item.get("text", "")
            return content

        return result

    async def read_resource(self, uri: str) -> Any:
        """
        Read a resource from the connected server.

        Args:
            uri: Resource URI

        Returns:
            Resource content
        """
        if not self.is_connected:
            raise RuntimeError(f"MCP client {self.id} not connected")

        response = await self._send_request(MCPMethod.RESOURCES_READ, {"uri": uri})

        if response.is_error:
            raise RuntimeError(f"Resource read failed: {response.get_error_message()}")

        return response.result

    async def get_prompt(self, name: str, arguments: dict[str, str] = None) -> Any:
        """
        Get a prompt from the connected server.

        Args:
            name: Prompt name
            arguments: Prompt arguments

        Returns:
            Prompt result
        """
        if not self.is_connected:
            raise RuntimeError(f"MCP client {self.id} not connected")

        response = await self._send_request(
            MCPMethod.PROMPTS_GET, {"name": name, "arguments": arguments or {}}
        )

        if response.is_error:
            raise RuntimeError(f"Prompt get failed: {response.get_error_message()}")

        return response.result

    async def ping(self) -> bool:
        """Ping server to check connectivity."""
        if not self._transport or not self._transport.is_connected():
            return False

        try:
            response = await self._send_request(MCPMethod.PING, {})
            return not response.is_error
        except Exception:
            return False

    async def _initialize(self):
        """Send initialize request to server."""
        response = await self._send_request(
            MCPMethod.INITIALIZE,
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {
                    "roots": {"listChanged": True},
                },
                "clientInfo": {"name": "navig", "version": "1.0.0"},
            },
        )

        if response.is_error:
            raise RuntimeError(f"MCP initialize failed: {response.get_error_message()}")

        # Parse server info and capabilities
        result = response.result or {}
        self._server_info = result.get("serverInfo", {})
        self._capabilities = MCPCapabilities.from_dict(result)

        logger.debug(f"MCP server: {self._server_info.get('name', 'unknown')}")

        # Send initialized notification
        await self._send_notification(MCPMethod.INITIALIZED, {})

    async def _discover_tools(self):
        """Discover available tools from server."""
        response = await self._send_request(MCPMethod.TOOLS_LIST, {})

        if response.is_error:
            logger.warning(f"Failed to list tools: {response.get_error_message()}")
            return

        result = response.result or {}
        tools = result.get("tools", [])

        for tool_data in tools:
            tool = MCPTool.from_dict(tool_data, server_id=self.id)
            self._tools[tool.name] = tool

    async def _discover_resources(self):
        """Discover available resources from server."""
        response = await self._send_request(MCPMethod.RESOURCES_LIST, {})

        if response.is_error:
            logger.warning(f"Failed to list resources: {response.get_error_message()}")
            return

        result = response.result or {}
        resources = result.get("resources", [])

        for res_data in resources:
            resource = MCPResource.from_dict(res_data, server_id=self.id)
            self._resources[resource.uri] = resource

    async def _discover_prompts(self):
        """Discover available prompts from server."""
        response = await self._send_request(MCPMethod.PROMPTS_LIST, {})

        if response.is_error:
            logger.warning(f"Failed to list prompts: {response.get_error_message()}")
            return

        result = response.result or {}
        prompts = result.get("prompts", [])

        for prompt_data in prompts:
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
        """Send request and wait for response."""
        self._request_id += 1

        request = JSONRPCRequest(
            method=method.value,
            params=params,
            id=self._request_id,
        )

        response_data = await self._transport.send(request.to_json())
        return JSONRPCResponse.from_json(response_data)

    async def _send_notification(self, method: MCPMethod, params: dict[str, Any]):
        """Send notification (no response expected)."""
        request = JSONRPCRequest(
            method=method.value,
            params=params,
            id=None,  # Notifications have no ID
        )

        await self._transport.send_notification(request.to_json())
