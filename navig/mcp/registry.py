"""MCP Client Manager and unified tool registry."""

from __future__ import annotations

import asyncio
from typing import Any

from navig.debug_logger import get_debug_logger

from .client import MCPClient, MCPClientConfig
from .protocol import MCPResource, MCPTool

logger = get_debug_logger()


class MCPClientManager:
    """Manages multiple MCP client connections.

    Provides:
    - A unified tool and resource registry across all connected servers.
    - Auto-connect for clients marked ``auto_connect=True``.
    - Retry logic on connection failure.
    - Tool routing: ``call_tool`` automatically picks the right client.

    Example::

        manager = MCPClientManager()
        await manager.add_client(MCPClientConfig(
            id="fs",
            command="npx",
            args=["-y", "@anthropic/mcp-server-filesystem", "/tmp"],
        ))
        await manager.start()

        for tool in manager.get_all_tools():
            print(tool.server_id, tool.name)

        result = await manager.call_tool("read_file", {"path": "/tmp/test.txt"})
        await manager.stop()
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}
        self._clients: dict[str, MCPClient] = {}
        self._reconnect_tasks: dict[str, asyncio.Task[None]] = {}
        # Strong references to fire-and-forget background tasks.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._started = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro: Any) -> None:
        """Schedule *coro* as a background task, keeping a strong reference."""
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    @property
    def clients(self) -> dict[str, MCPClient]:
        return self._clients

    def get_connected_clients(self) -> list[MCPClient]:
        return [c for c in self._clients.values() if c.is_connected]

    def get_all_tools(self) -> list[MCPTool]:
        tools: list[MCPTool] = []
        for client in self._clients.values():
            if client.is_connected:
                tools.extend(client.tools)
        return tools

    def get_all_resources(self) -> list[MCPResource]:
        resources: list[MCPResource] = []
        for client in self._clients.values():
            if client.is_connected:
                resources.extend(client.resources)
        return resources

    def find_tool(self, name: str) -> tuple[MCPClient, MCPTool] | None:
        """Return ``(client, tool)`` for the first client that exposes *name*, or ``None``."""
        for client in self._clients.values():
            if not client.is_connected:
                continue
            for tool in client.tools:
                if tool.name == name:
                    return client, tool
        return None

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """Call *name* on whichever connected client advertises it.

        Args:
            name:      Tool name.
            arguments: Tool arguments; ``None`` is normalised to ``{}``.

        Raises:
            ValueError: If no connected client has the tool.
        """
        result = self.find_tool(name)
        if result is None:
            available = [t.name for t in self.get_all_tools()]
            raise ValueError(
                f"Tool {name!r} not found on any connected client "
                f"(available: {available})"
            )
        client, _ = result
        return await client.call_tool(name, arguments or {})

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    async def add_client(self, config: MCPClientConfig) -> MCPClient:
        """Register and optionally auto-connect a client.

        If ``config.auto_connect`` is ``True`` the connection attempt starts
        immediately in the background, regardless of whether :meth:`start` has
        been called.
        """
        client = MCPClient(config)
        self._clients[config.id] = client

        if config.auto_connect:
            self._fire_and_forget(self._connect_with_retry(client))

        return client

    async def remove_client(self, client_id: str) -> None:
        """Disconnect and deregister a client."""
        client = self._clients.pop(client_id, None)
        if client is not None:
            await client.disconnect()

        task = self._reconnect_tasks.pop(client_id, None)
        if task is not None:
            task.cancel()

    async def start(self) -> None:
        """Start the manager and auto-connect all configured clients.

        Client configs are read from ``self.config['mcp']['clients']``.
        """
        if self._started:
            return

        self._started = True

        mcp_config: dict[str, Any] = (
            self.config.get("mcp", {}).get("clients", {})
        )
        for client_id, client_cfg in mcp_config.items():
            if not client_cfg.get("enabled", True):
                continue

            cfg = MCPClientConfig.from_dict(client_id, client_cfg)
            client = MCPClient(cfg)
            self._clients[client_id] = client

            if cfg.auto_connect:
                self._fire_and_forget(self._connect_with_retry(client))

        logger.info(
            "MCP Client Manager started with %d client(s)", len(self._clients)
        )

    async def stop(self) -> None:
        """Disconnect all clients and cancel all background tasks."""
        self._started = False

        for task in self._reconnect_tasks.values():
            task.cancel()
        self._reconnect_tasks.clear()

        if self._clients:
            await asyncio.gather(
                *[c.disconnect() for c in self._clients.values()],
                return_exceptions=True,
            )
        self._clients.clear()

        logger.info("MCP Client Manager stopped")

    async def connect_client(self, client_id: str) -> bool:
        """Connect a specific registered client.  Returns ``True`` on success."""
        client = self._clients.get(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id!r}")
        await self._connect_with_retry(client, max_attempts=1)
        return client.is_connected

    async def disconnect_client(self, client_id: str) -> bool:
        """Disconnect a specific client.  Returns ``True`` if found."""
        client = self._clients.get(client_id)
        if client is None:
            return False

        task = self._reconnect_tasks.pop(client_id, None)
        if task is not None:
            task.cancel()

        await client.disconnect()
        return True

    async def reconnect_client(self, client_id: str) -> bool:
        """Disconnect and reconnect a specific client.  Returns ``True`` on success."""
        client = self._clients.get(client_id)
        if client is None:
            raise ValueError(f"Client not found: {client_id!r}")
        await client.disconnect()
        await self._connect_with_retry(client, max_attempts=1)
        return client.is_connected

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return a status snapshot for all registered clients."""
        clients = [
            {
                "id": client_id,
                "connected": client.is_connected,
                "tools_count": len(client.tools) if client.is_connected else 0,
                "resources_count": len(client.resources) if client.is_connected else 0,
                # Access the private attribute only for status reporting.
                "server_info": client._server_info if client.is_connected else None,
            }
            for client_id, client in self._clients.items()
        ]
        return {
            "clients": clients,
            "total_tools": len(self.get_all_tools()),
            "total_resources": len(self.get_all_resources()),
        }

    # ------------------------------------------------------------------
    # Retry / reconnect internals
    # ------------------------------------------------------------------

    async def _connect_with_retry(
        self,
        client: MCPClient,
        max_attempts: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        """Attempt to connect *client*, retrying up to *max_attempts* times."""
        for attempt in range(max_attempts):
            try:
                await client.connect()
                logger.info("MCP client %s connected", client.id)
                return
            except Exception as exc:
                logger.warning(
                    "MCP client %s connect failed (attempt %d/%d): %s",
                    client.id,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts - 1:
                    # Exponential back-off: 5 s, 10 s, 15 s, …
                    await asyncio.sleep(retry_delay * (attempt + 1))

        logger.error(
            "MCP client %s failed to connect after %d attempt(s)",
            client.id,
            max_attempts,
        )

    async def _schedule_reconnect(
        self, client: MCPClient, delay: float = 30.0
    ) -> None:
        """Schedule a single reconnect attempt for *client* after *delay* seconds."""
        if client.id in self._reconnect_tasks:
            return  # Already scheduled

        async def _reconnect() -> None:
            await asyncio.sleep(delay)
            if not client.is_connected and self._started:
                await self._connect_with_retry(client)
            self._reconnect_tasks.pop(client.id, None)

        self._reconnect_tasks[client.id] = asyncio.create_task(
            _reconnect(), name=f"mcp-reconnect-{client.id}"
        )
