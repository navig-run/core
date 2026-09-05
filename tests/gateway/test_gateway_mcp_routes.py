"""The gateway's MCP control surface was DOA — 4 of its 5 endpoints crashed.

`core/navig/gateway/routes/mcp.py` called APIs that do not exist on the objects it
was handed (the same class as the Supabase connector being DOA on every method, and
the wrong-shape dataclass constructions swept in #701):

* ``GET  /mcp/clients``            -> ``client.connected``            (it is ``is_connected``)
* ``GET  /mcp/tools``             -> ``manager.list_tools()``        (it is ``get_all_tools()``)
                                     and ``tool.client_name``        (it is ``server_id``)
* ``POST /mcp/tools/{name}/call`` -> ``call_tool(tool_name=...)``    (the parameter is ``name``)
* ``POST /mcp/connect``           -> ``add_client(name=, command=, url=)``
                                     (it takes ONE ``MCPClientConfig``) + ``client.connected``

The first two raised an unhandled ``AttributeError`` (aiohttp 500); the last two were
caught by their ``except Exception`` and always returned a 500 body. Only
``POST /mcp/disconnect`` ever worked.

These tests use ``create_autospec`` deliberately: a plain ``MagicMock`` invents any
attribute you ask for and ignores call signatures, so it would have happily returned a
mock for ``client.connected`` and let ``call_tool(tool_name=...)`` through — the bug
would stay invisible. Autospec enforces both attribute existence and signatures.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from navig.gateway.routes import mcp as mcp_routes
from navig.mcp.client import MCPClient
from navig.mcp.protocol import MCPTool
from navig.mcp.registry import MCPClientManager


@pytest.fixture(autouse=True)
def _no_auth():
    """Every handler starts with require_bearer_auth; None means 'allowed'."""
    with patch("navig.gateway.routes.mcp.require_bearer_auth", return_value=None):
        yield


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def _gateway(manager) -> MagicMock:
    gw = MagicMock()
    gw.mcp_client_manager = manager
    return gw


def _request(*, match: dict | None = None, payload: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.match_info = match or {}
    r.json = AsyncMock(return_value=payload if payload is not None else {})
    return r


def _manager() -> MagicMock:
    return create_autospec(MCPClientManager, instance=True)


def _client(*, connected: bool) -> MagicMock:
    c = create_autospec(MCPClient, instance=True)
    c.is_connected = connected
    return c


class TestClientsEndpoint:
    async def test_reports_each_client_connection_state(self):
        manager = _manager()
        manager.clients = {"srv": _client(connected=True), "down": _client(connected=False)}

        resp = await mcp_routes._clients(_gateway(manager))(_request())

        assert resp.status == 200
        body = _body(resp)
        assert body["ok"] is True
        assert body["data"]["clients"] == [
            {"name": "srv", "connected": True},
            {"name": "down", "connected": False},
        ]


class TestToolsEndpoint:
    async def test_lists_tools_with_their_owning_server(self):
        manager = _manager()
        manager.get_all_tools.return_value = [
            MCPTool(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object"},
                server_id="fs",
            )
        ]

        resp = await mcp_routes._tools(_gateway(manager))(_request())

        assert resp.status == 200
        assert _body(resp)["data"]["tools"] == [
            {"name": "read_file", "description": "Read a file", "client": "fs"}
        ]


class TestCallToolEndpoint:
    async def test_calls_the_manager_with_its_real_signature(self):
        manager = _manager()
        manager.call_tool = AsyncMock(return_value={"content": "hi"})

        resp = await mcp_routes._call_tool(_gateway(manager))(
            _request(match={"tool_name": "read_file"}, payload={"arguments": {"path": "/x"}})
        )

        assert resp.status == 200, _body(resp)
        assert _body(resp)["data"]["result"] == {"content": "hi"}
        # The manager's parameter is `name`; `tool_name=` was a TypeError.
        manager.call_tool.assert_awaited_once_with("read_file", {"path": "/x"})


class TestConnectEndpoint:
    async def test_builds_a_client_config_for_a_stdio_server(self):
        manager = _manager()
        manager.add_client = AsyncMock(return_value=_client(connected=False))

        resp = await mcp_routes._connect(_gateway(manager))(
            _request(payload={"name": "fs", "command": "npx", "args": ["srv"]})
        )

        assert resp.status == 200, _body(resp)
        assert _body(resp)["data"]["name"] == "fs"
        cfg = manager.add_client.await_args.args[0]  # ONE positional MCPClientConfig
        assert cfg.id == "fs"
        assert cfg.command == "npx"
        assert cfg.args == ["srv"]
        assert cfg.transport == "stdio"

    async def test_url_only_request_infers_an_http_transport(self):
        """transport defaults to 'stdio'; a url with no command would otherwise build a
        stdio transport with no command to run."""
        manager = _manager()
        manager.add_client = AsyncMock(return_value=_client(connected=False))

        resp = await mcp_routes._connect(_gateway(manager))(
            _request(payload={"name": "remote", "url": "https://mcp.example.com/sse"})
        )

        assert resp.status == 200, _body(resp)
        cfg = manager.add_client.await_args.args[0]
        assert cfg.url == "https://mcp.example.com/sse"
        assert cfg.transport == "sse"

    async def test_explicit_transport_is_respected(self):
        manager = _manager()
        manager.add_client = AsyncMock(return_value=_client(connected=False))

        await mcp_routes._connect(_gateway(manager))(
            _request(
                payload={
                    "name": "ws",
                    "url": "wss://mcp.example.com",
                    "transport": "websocket",
                }
            )
        )

        assert manager.add_client.await_args.args[0].transport == "websocket"

    async def test_missing_name_is_a_400_not_a_500(self):
        manager = _manager()
        manager.add_client = AsyncMock()

        resp = await mcp_routes._connect(_gateway(manager))(_request(payload={"command": "npx"}))

        assert resp.status == 400
        assert _body(resp)["ok"] is False


class TestDisconnectEndpoint:
    async def test_still_removes_the_client(self):
        """The one endpoint that always worked — guard it while fixing its neighbours."""
        manager = _manager()
        manager.remove_client = AsyncMock()

        resp = await mcp_routes._disconnect(_gateway(manager))(_request(payload={"name": "fs"}))

        assert resp.status == 200
        assert _body(resp)["data"]["disconnected"] is True
        manager.remove_client.assert_awaited_once_with("fs")
