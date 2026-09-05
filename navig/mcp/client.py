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
        # `navig config set mcp.clients.<id>.enabled false` stores the STRING
        # "false" (truthy) — coerce the booleans so a config-set toggle actually
        # takes effect, instead of a raw `data.get("enabled", True)` reading it as ON.
        from navig.core.coerce import coerce_bool

        return cls(
            id=id,
            command=data.get("command"),
            args=data.get("args", []),
            env=data.get("env", {}),
            transport=data.get("transport", "stdio"),
            url=data.get("url"),
            cwd=data.get("cwd"),
            auto_connect=coerce_bool(data.get("auto_connect"), default=True),
            enabled=coerce_bool(data.get("enabled"), default=True),
        )


def _error_text_from_content(content: Any) -> str:
    """Join the text parts of an MCP content array into a readable error message."""
    if isinstance(content, list):
        parts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        joined = " ".join(p for p in parts if p)
        if joined:
            return joined
    if isinstance(content, str) and content:
        return content
    return "remote tool reported an error"


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
        #: Fingerprint of the tool claims a trust decision was made against, so a
        #: server changing them under us is visible. See `_note_catalog_revision`.
        self._catalog_revision: str | None = None
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
        # A tool-level failure rides on a SUCCESSFUL JSON-RPC response as
        # ``result.isError == true`` (MCP tool errors are result-level, not
        # protocol errors, so ``response.is_error`` above is False). Surface it
        # like the JSON-RPC error — otherwise the error text is returned as a
        # normal value and the caller treats a failed remote tool as a success.
        # (Mirror of the server-side isError fix.)
        if isinstance(result, dict) and result.get("isError"):
            raise RuntimeError(
                f"Tool call {name!r} failed: "
                f"{_error_text_from_content(result.get('content'))}"
            )
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
        raw_tools = (response.result or {}).get("tools", [])
        raw_tools = self._cap_tools(raw_tools)
        for tool_data in raw_tools:
            # Guard each item: a single malformed entry (e.g. missing "name") from an
            # external server must not abort discovery — connect() catches any raise,
            # disconnects, and fails the WHOLE client, losing every other tool.
            try:
                tool = MCPTool.from_dict(tool_data, server_id=self.id)
                self._tools[tool.name] = tool
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP %s: skipping malformed tool entry: %s", self.id, exc)

        self._note_catalog_revision(raw_tools)

    def _cap_tools(self, raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bound how many tools one server may contribute.

        Every discovered tool becomes an entry in the agent's tool schema, which is sent
        to the model on **every** request. A server advertising thousands of tools
        therefore inflates the prompt — and the bill — without ever being called, and
        nothing in the protocol stops it from doing so. The cap makes an untrusted
        endpoint's footprint bounded rather than whatever it decides.

        Never a silent truncation: a catalog that was cut says so, and names the
        numbers, because "200 tools" and "200 of 4000 tools" are different situations.
        """
        from navig.mcp.trust import MAX_TOOLS_PER_SERVER

        if len(raw_tools) <= MAX_TOOLS_PER_SERVER:
            return raw_tools
        logger.warning(
            "MCP %s: advertised %d tools; using the first %d. The rest are ignored — "
            "scope this server to the tools you need with "
            "`navig config set mcp.trust.servers.%s.tools \"a,b\"`.",
            self.id,
            len(raw_tools),
            MAX_TOOLS_PER_SERVER,
            self.id,
        )
        return raw_tools[:MAX_TOOLS_PER_SERVER]

    def _note_catalog_revision(self, raw_tools: list[dict[str, Any]]) -> None:
        """Record — and report — a change to the claims this server's grant rests on.

        Trusting a server is a statement about the tools it offered when the operator
        vouched for it. A server that later adds a tool, or flips ``readOnlyHint`` on an
        existing one, silently widens what runs without asking. The fingerprint covers
        exactly the claims a decision was made against and excludes descriptions, so a
        copy edit stays quiet.

        Best-effort and non-fatal: this is a notice, not a gate. Nothing here may
        prevent a server from connecting.
        """
        try:
            from navig.mcp.trust import catalog_revision

            revision = catalog_revision(raw_tools)
            previous = self._catalog_revision
            self._catalog_revision = revision
            if previous is not None and previous != revision:
                logger.warning(
                    "MCP %s: tool catalog changed (%s -> %s). Re-check what this server "
                    "offers before relying on an existing trust setting.",
                    self.id,
                    previous,
                    revision,
                )
        except Exception as exc:  # noqa: BLE001 — a notice must never break discovery
            logger.debug("MCP %s: could not fingerprint tool catalog: %s", self.id, exc)

    async def _discover_resources(self) -> None:
        response = await self._send_request(MCPMethod.RESOURCES_LIST, {})
        if response.is_error:
            logger.warning(
                "Failed to list resources: %s", response.get_error_message()
            )
            return
        for res_data in (response.result or {}).get("resources", []):
            try:
                resource = MCPResource.from_dict(res_data, server_id=self.id)
                self._resources[resource.uri] = resource
            except Exception as exc:  # noqa: BLE001 - one bad resource must not drop the rest
                logger.warning("MCP %s: skipping malformed resource entry: %s", self.id, exc)

    async def _discover_prompts(self) -> None:
        response = await self._send_request(MCPMethod.PROMPTS_LIST, {})
        if response.is_error:
            logger.warning(
                "Failed to list prompts: %s", response.get_error_message()
            )
            return
        for prompt_data in (response.result or {}).get("prompts", []):
            try:
                prompt = MCPPrompt(
                    name=prompt_data["name"],
                    description=prompt_data.get("description"),
                    arguments=prompt_data.get("arguments", []),
                    server_id=self.id,
                )
                self._prompts[prompt.name] = prompt
            except Exception as exc:  # noqa: BLE001 - one bad prompt must not drop the rest
                logger.warning("MCP %s: skipping malformed prompt entry: %s", self.id, exc)

    async def _send_request(
        self, method: MCPMethod, params: dict[str, Any]
    ) -> JSONRPCResponse:
        self._request_id += 1
        request = JSONRPCRequest(
            method=method.value, params=params, id=self._request_id
        )
        assert self._transport is not None  # guarded by _assert_connected callers  # noqa: S101
        response_data = await self._transport.send(request.to_json())
        return JSONRPCResponse.from_json(response_data)

    async def _send_notification(
        self, method: MCPMethod, params: dict[str, Any]
    ) -> None:
        request = JSONRPCRequest(
            method=method.value, params=params, id=None
        )
        assert self._transport is not None  # noqa: S101
        await self._transport.send_notification(request.to_json())
