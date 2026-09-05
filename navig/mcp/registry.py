"""MCP Client Manager and unified tool registry."""

from __future__ import annotations

import asyncio
from typing import Any

from navig.debug_logger import get_debug_logger

from .client import MCPClient, MCPClientConfig
from .protocol import MCPResource, MCPTool

logger = get_debug_logger()

# How often the health sweep looks for clients that should be connected but aren't.
# A client can drop at any time (the server exits, the network blips, or the transport's
# reader task dies) and nothing pushes that to the manager — is_connected() is a pull, so
# somebody has to look.
_HEALTH_INTERVAL_S = 60.0


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
        self._health_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro: Any) -> None:
        """Schedule *coro* as a background task, keeping a strong reference."""
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _installed_plugin_mcp_servers(self) -> dict[str, dict[str, Any]]:
        """MCP servers declared by installed plugins (`.mcp.json`), keyed by name.

        Reuses the plugin host's parsed `mcp_servers`; normalises the Claude Code
        `type` field to `transport`. Best-effort — a bad plugin is skipped, never
        fatal, so it can't block MCP startup (degraded-never-blocks-boot)."""
        out: dict[str, dict[str, Any]] = {}
        try:
            from navig.plugins.package import installed_plugin_roots, load_package
        except Exception:  # noqa: BLE001
            return out
        for root in installed_plugin_roots():
            try:
                pkg = load_package(root)
            except Exception:  # noqa: BLE001
                continue
            for name, cfg in (pkg.mcp_servers or {}).items():
                if not isinstance(cfg, dict):
                    continue
                norm = dict(cfg)
                if "transport" not in norm and "type" in norm:
                    norm["transport"] = norm.get("type")
                out.setdefault(name, norm)
        return out

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
        """Return ``(client, tool)`` for the connected client that exposes *name*.

        Raises:
            ValueError: If more than one connected client advertises *name*.

        Ambiguity is refused rather than resolved. This used to return the first match
        in ``dict`` order, so which of two remote systems served a duplicated name
        depended on connection order — a silent pick between, say, two different
        ``deploy`` tools. Address the tool as ``<server>:<tool>`` to disambiguate.
        """
        matches = [
            (client, tool)
            for client in self._clients.values()
            if client.is_connected
            for tool in client.tools
            if tool.name == name
        ]
        if len(matches) > 1:
            servers = sorted(client.id for client, _ in matches)
            raise ValueError(
                f"Tool {name!r} is ambiguous — advertised by {len(matches)} connected "
                f"servers: {', '.join(servers)}. Qualify it as "
                f"'{servers[0]}:{name}' to choose one."
            )
        return matches[0] if matches else None

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """Call *name* on whichever connected client advertises it.

        The tool belongs to a third party, so it passes the approval gate first. This
        path consulted **no gate at all** before the trust boundary landed —
        ``navig.tools.approval`` was not imported anywhere in this module,
        ``client.py`` or ``gateway/routes/mcp.py`` — so an operator's deny could not
        even be expressed, let alone honoured.

        Args:
            name:      Tool name, optionally qualified as ``<server>:<tool>``.
            arguments: Tool arguments; ``None`` is normalised to ``{}``.

        Raises:
            ValueError:      If no connected client has the tool, or the name is
                             ambiguous across servers.
            PermissionError: If the operator did not approve the call.
        """
        # Only read a `<server>:` prefix when that server is actually registered.
        # Nothing stops a server naming a tool `foo:bar`, and splitting such a name
        # unconditionally would send the lookup to a server called "foo".
        head, _, tail = name.partition(":")
        server_hint = head if tail and head in self._clients else ""
        lookup = tail if server_hint else name

        if server_hint:
            client = self._clients[server_hint]
            tool = next((t for t in client.tools if t.name == lookup), None)
            if tool is None or not client.is_connected:
                raise ValueError(
                    f"Tool {lookup!r} not found on connected server {server_hint!r}"
                )
            result = (client, tool)
        else:
            result = self.find_tool(lookup)
            if result is None:
                available = [t.name for t in self.get_all_tools()]
                raise ValueError(
                    f"Tool {lookup!r} not found on any connected client "
                    f"(available: {available})"
                )

        client, tool = result
        await self._authorize(client, tool, arguments or {})
        return await client.call_tool(lookup, arguments or {})

    async def _authorize(
        self, client: MCPClient, tool: MCPTool, arguments: dict[str, Any]
    ) -> None:
        """Hold a third-party tool call for the operator unless it is a declared read.

        Fails closed: any error reaching a decision denies the call. A gate that breaks
        must not become a gate that waves things through.
        """
        from navig.mcp.trust import (
            allowed_tools_for_server,
            auto_approve_tools_for_server,
            classify_tool,
            honor_read_only_hint,
            tool_is_in_scope,
            trust_for_server,
        )
        from navig.tools.approval import (
            ApprovalDecision,
            audit_auto_approval,
            external_tool_was_pre_authorised,
            get_approval_gate,
            needs_approval,
            record_external_tool,
        )
        from navig.tools.untrusted_text import describe_call

        # Scope is checked BEFORE trust: trust says how far the server's claims are
        # believed, scope says which tools it may offer at all. A tool outside the
        # allowlist is refused outright rather than offered for approval — asking about
        # a call the operator has already ruled out is noise, and answering "yes" would
        # contradict the config.
        if not tool_is_in_scope(tool.name, allowed_tools_for_server(client.id)):
            raise PermissionError(
                f"Tool {tool.name!r} is outside the configured scope for MCP server "
                f"{client.id!r}. Allow it with: navig config set "
                f"mcp.trust.servers.{client.id}.tools \"{tool.name},...\""
            )

        try:
            classified = classify_tool(
                tool.name,
                server=client.id,
                annotations=tool.annotations,
                trust=trust_for_server(client.id),
                description=tool.description,
                honor_read_only=honor_read_only_hint(),
            )
            # Gate 2 of 2. Gate 1 is `classified.auto_approvable` — the SERVER's claim,
            # on a vetted endpoint, that this action is non-destructive and idempotent.
            # This one is the OPERATOR's: they named the tool. Neither party can release
            # an action alone.
            pre_authorised = tool.name in auto_approve_tools_for_server(client.id)

            record_external_tool(
                classified.registry_name,
                mode=classified.mode.value,
                auto_approvable=classified.auto_approvable,
                operator_pre_authorised=pre_authorised,
                server=client.id,
            )

            if not needs_approval(classified.registry_name):
                # A read was never going to ask. An ACTION that gets here ran because
                # both gates agreed, and that must still appear in the audit log — an
                # unprompted write nobody can account for afterwards is the thing this
                # whole mechanism is supposed to avoid.
                if external_tool_was_pre_authorised(classified.registry_name):
                    audit_auto_approval(
                        classified.registry_name,
                        enabled_by=f"mcp.trust.servers.{client.id}.auto_approve",
                        parameters=arguments,
                    )
                    logger.info(
                        "MCP %s: %r ran without a prompt (pre-authorised, and the server "
                        "declares it non-destructive + idempotent)",
                        client.id,
                        tool.name,
                    )
                return

            title, description = describe_call(
                server_name=client.id,
                endpoint=client.config.url or client.config.command or client.id,
                tool_name=tool.name,
                description=tool.description,
                arguments=arguments,
                mode=classified.mode.value,
                classified_by=classified.classified_by.value,
            )
            decision = await get_approval_gate().check(
                tool_name=classified.registry_name,
                safety_level="dangerous",
                parameters=arguments,
                reason=title,
                context={"description": description, "mcp_server": client.id},
            )
        except Exception as exc:  # noqa: BLE001 — interlock broke -> deny, never proceed
            logger.error(
                "MCP approval interlock failed for %r on %r — denying (fail closed): %s",
                tool.name,
                client.id,
                exc,
            )
            raise PermissionError(
                f"Approval gate error for MCP tool {tool.name!r} — failing closed"
            ) from exc

        if decision != ApprovalDecision.APPROVED:
            raise PermissionError(
                f"Operator did not approve MCP tool {tool.name!r} on server "
                f"{client.id!r}. If this server's tools are trustworthy, classify it "
                f"with: navig config set mcp.trust.servers.{client.id} vetted"
            )

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
            # Keep it connected: without this a drop (or a failed initial connect)
            # is never retried. Every production call site enters here, not start().
            self._ensure_health_loop()

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

        mcp_config: dict[str, Any] = dict(
            self.config.get("mcp", {}).get("clients", {})
        )
        # Merge MCP servers declared by installed CC/NAVIG plugins (`.mcp.json`).
        # Config-defined clients win — a plugin never overrides an explicit client.
        for client_id, client_cfg in self._installed_plugin_mcp_servers().items():
            mcp_config.setdefault(client_id, client_cfg)

        for client_id, client_cfg in mcp_config.items():
            # from_dict coerces the `enabled`/`auto_connect` booleans (the raw-string
            # config gotcha), so gate on the parsed cfg — a `config set …enabled false`
            # now actually skips the client instead of reading "false" as truthy.
            cfg = MCPClientConfig.from_dict(client_id, client_cfg)
            if not cfg.enabled:
                continue

            client = MCPClient(cfg)
            self._clients[client_id] = client

            if cfg.auto_connect:
                self._fire_and_forget(self._connect_with_retry(client))
                self._ensure_health_loop()

        logger.info(
            "MCP Client Manager started with %d client(s)", len(self._clients)
        )

    async def stop(self) -> None:
        """Disconnect all clients and cancel all background tasks."""
        self._started = False

        if self._health_task is not None:
            self._health_task.cancel()
            self._health_task = None

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
            try:
                await asyncio.sleep(delay)
                # Gate on the client still being REGISTERED, not on self._started:
                # production never calls start() — every live call site uses
                # add_client() (gateway/server.py, gateway/routes/mcp.py,
                # daemon/telegram_worker.py), so _started stays False and this
                # reconnect used to no-op. remove_client()/stop() clear _clients,
                # so registration is the accurate "should be connected" signal.
                if not client.is_connected and client.id in self._clients:
                    await self._connect_with_retry(client)
            finally:
                # Always release the dedupe slot — otherwise one raising reconnect
                # (or a cancel) would leave the id parked in _reconnect_tasks and
                # every future attempt would return early as "already scheduled".
                self._reconnect_tasks.pop(client.id, None)

        self._reconnect_tasks[client.id] = asyncio.create_task(
            _reconnect(), name=f"mcp-reconnect-{client.id}"
        )

    def _ensure_health_loop(self) -> None:
        """Start the health sweep once (idempotent); safe to call from every entry point."""
        if self._health_task is not None and not self._health_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (sync construction in a test) — nothing to schedule
        self._health_task = asyncio.create_task(
            self._health_loop(), name="mcp-health-sweep"
        )

    async def _health_loop(self) -> None:
        """Reconnect clients that should be connected but aren't.

        Nothing pushes a dropped connection to the manager: a server can exit, the
        network can blip, or a transport's reader task can die (#692/#694 made that
        state honestly visible as ``is_connected == False``). Without this sweep the
        client is simply filtered out of ``find_tool``/``get_all_tools`` and stays
        dead for the daemon's whole lifetime — its tools silently gone.
        """
        while True:
            await asyncio.sleep(_HEALTH_INTERVAL_S)
            try:
                for client in list(self._clients.values()):
                    # Per-client guard: one client whose config/state raises must not
                    # kill the sweep for every other client.
                    try:
                        if client.config.auto_connect and not client.is_connected:
                            logger.info(
                                "MCP client %s is down — scheduling reconnect", client.id
                            )
                            await self._schedule_reconnect(client, delay=0.0)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "MCP health sweep skipped client %s: %s",
                            getattr(client, "id", "?"),
                            exc,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # The sweep must survive an unexpected error — a loop that dies here
                # silently restores the "dead forever" behaviour it exists to prevent.
                logger.warning("MCP health sweep iteration failed: %s", exc)
