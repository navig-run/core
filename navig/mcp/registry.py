"""MCP Client Manager and Tool Registry."""

import asyncio
from typing import TYPE_CHECKING, Any

from navig.debug_logger import get_debug_logger

from .client import MCPClient, MCPClientConfig
from .protocol import MCPResource, MCPTool

if TYPE_CHECKING:
    pass

logger = get_debug_logger()


class MCPClientManager:
    """
    Manages multiple MCP client connections.

    Provides:
    - Unified tool registry across all connected servers
    - Auto-connect for configured servers
    - Reconnection handling
    - Tool routing to correct client

    Example:
        manager = MCPClientManager()

        # Add clients from config
        await manager.add_client(MCPClientConfig(
            id="filesystem",
            command="npx",
            args=["-y", "@anthropic/mcp-server-filesystem", "/tmp"],
        ))

        await manager.start()

        # Get all tools across clients
        for tool in manager.get_all_tools():
            print(f"{tool.server_id}/{tool.name}")

        # Call a tool (automatically routes to correct client)
        result = await manager.call_tool("read_file", {"path": "/tmp/test.txt"})

        await manager.stop()
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._clients: dict[str, MCPClient] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        self._started = False

    @property
    def clients(self) -> dict[str, MCPClient]:
        """Get all registered clients."""
        return self._clients

    def get_connected_clients(self) -> list[MCPClient]:
        """Get all connected clients."""
        return [c for c in self._clients.values() if c.is_connected]

    def get_all_tools(self) -> list[MCPTool]:
        """Get tools from all connected clients."""
        tools = []
        for client in self._clients.values():
            if client.is_connected:
                tools.extend(client.tools)
        return tools

    def get_all_resources(self) -> list[MCPResource]:
        """Get resources from all connected clients."""
        resources = []
        for client in self._clients.values():
            if client.is_connected:
                resources.extend(client.resources)
        return resources

    def find_tool(self, name: str) -> tuple[MCPClient, MCPTool] | None:
        """
        Find tool by name across all clients.

        Returns (client, tool) tuple or None if not found.
        """
        for client in self._clients.values():
            if client.is_connected:
                for tool in client.tools:
                    if tool.name == name:
                        return client, tool
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any] = None) -> Any:
        """
        Call a tool, routing to the correct client.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool result

        Raises:
            ValueError: If tool not found in any client
        """
        result = self.find_tool(name)
        if not result:
            available = [t.name for t in self.get_all_tools()]
            raise ValueError(f"Tool not found: {name} (available: {available})")

        client, tool = result
        return await client.call_tool(name, arguments)

    async def add_client(self, config: MCPClientConfig) -> MCPClient:
        """
        Add a new MCP client.

        If manager is already started and config.auto_connect is True,
        the client will be connected immediately.
        """
        client = MCPClient(config)
        self._clients[config.id] = client

        if self._started and config.auto_connect:
            await self._connect_with_retry(client)

        return client

    async def remove_client(self, client_id: str):
        """Remove and disconnect a client."""
        client = self._clients.pop(client_id, None)
        if client:
            await client.disconnect()

        # Cancel any reconnect task
        task = self._reconnect_tasks.pop(client_id, None)
        if task:
            task.cancel()

    async def start(self):
        """
        Start manager and auto-connect configured clients.

        Loads client configs from self.config['mcp']['clients'].
        """
        if self._started:
            return

        self._started = True

        # Load clients from config
        mcp_config = self.config.get("mcp", {}).get("clients", {})

        for client_id, client_cfg in mcp_config.items():
            if not client_cfg.get("enabled", True):
                continue

            config = MCPClientConfig.from_dict(client_id, client_cfg)
            client = MCPClient(config)
            self._clients[client_id] = client

            if config.auto_connect:
                # Connect in background to not block startup
                asyncio.create_task(self._connect_with_retry(client))

        logger.info(f"MCP Client Manager started with {len(self._clients)} clients")

    async def stop(self):
        """Stop all clients."""
        self._started = False

        # Cancel reconnect tasks
        for task in self._reconnect_tasks.values():
            task.cancel()
        self._reconnect_tasks.clear()

        # Disconnect all clients
        disconnect_tasks = [client.disconnect() for client in self._clients.values()]
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)

        self._clients.clear()
        logger.info("MCP Client Manager stopped")

    async def connect_client(self, client_id: str) -> bool:
        """
        Connect a specific client.

        Returns True if connected successfully.
        """
        client = self._clients.get(client_id)
        if not client:
            raise ValueError(f"Client not found: {client_id}")

        await self._connect_with_retry(client, max_attempts=1)
        return client.is_connected

    async def disconnect_client(self, client_id: str) -> bool:
        """
        Disconnect a specific client.

        Returns True if client was found and disconnected.
        """
        client = self._clients.get(client_id)
        if not client:
            return False

        # Cancel reconnect task if any
        task = self._reconnect_tasks.pop(client_id, None)
        if task:
            task.cancel()

        await client.disconnect()
        return True

    async def reconnect_client(self, client_id: str) -> bool:
        """
        Reconnect a specific client.

        Returns True if reconnected successfully.
        """
        client = self._clients.get(client_id)
        if not client:
            raise ValueError(f"Client not found: {client_id}")

        await client.disconnect()
        await self._connect_with_retry(client, max_attempts=1)
        return client.is_connected

    def get_status(self) -> dict[str, Any]:
        """
        Get status of all clients.

        Returns dict with client statuses.
        """
        clients = []
        for client_id, client in self._clients.items():
            clients.append(
                {
                    "id": client_id,
                    "connected": client.is_connected,
                    "tools_count": len(client.tools) if client.is_connected else 0,
                    "resources_count": (
                        len(client.resources) if client.is_connected else 0
                    ),
                    "server_info": client._server_info if client.is_connected else None,
                }
            )

        return {
            "clients": clients,
            "total_tools": len(self.get_all_tools()),
            "total_resources": len(self.get_all_resources()),
        }

    async def _connect_with_retry(
        self,
        client: MCPClient,
        max_attempts: int = 3,
        retry_delay: float = 5.0,
    ):
        """Connect client with retry on failure."""
        for attempt in range(max_attempts):
            try:
                await client.connect()
                logger.info(f"MCP client {client.id} connected")
                return
            except Exception as e:
                logger.warning(
                    f"MCP client {client.id} connect failed (attempt {attempt + 1}/{max_attempts}): {e}"
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))

        logger.error(
            f"MCP client {client.id} failed to connect after {max_attempts} attempts"
        )

    async def _schedule_reconnect(self, client: MCPClient, delay: float = 30.0):
        """Schedule reconnection attempt for a client."""
        if client.id in self._reconnect_tasks:
            return

        async def reconnect():
            await asyncio.sleep(delay)
            if not client.is_connected and self._started:
                await self._connect_with_retry(client)
            self._reconnect_tasks.pop(client.id, None)

        self._reconnect_tasks[client.id] = asyncio.create_task(reconnect())
