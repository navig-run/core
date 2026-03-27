"""MCP routes: /mcp/clients, tools, tools/{name}/call, connect, disconnect."""

from __future__ import annotations

try:
    from aiohttp import web  # noqa: F401
except ImportError as _exc:
    raise RuntimeError(
        "aiohttp is required for gateway routes (pip install aiohttp)"
    ) from _exc
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    json_error_response,
    json_ok,
    require_bearer_auth,
)

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/mcp/clients", _clients(gateway))
    app.router.add_get("/mcp/tools", _tools(gateway))
    app.router.add_post("/mcp/tools/{tool_name}/call", _call_tool(gateway))
    app.router.add_post("/mcp/connect", _connect(gateway))
    app.router.add_post("/mcp/disconnect", _disconnect(gateway))


def _chk(gw):
    if not gw.mcp_client_manager:
        return json_error_response(
            "MCP module not available", status=503, code="module_unavailable"
        )
    return None


def _clients(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        clients = []
        for name, client in gw.mcp_client_manager.clients.items():
            clients.append({"name": name, "connected": client.connected})
        return json_ok({"clients": clients})

    return h


def _tools(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        tools = gw.mcp_client_manager.list_tools()
        return json_ok(
            {
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "client": t.client_name,
                    }
                    for t in tools
                ]
            }
        )

    return h


def _call_tool(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        try:
            tool_name = r.match_info["tool_name"]
            data = await r.json()
            result = await gw.mcp_client_manager.call_tool(
                tool_name=tool_name,
                arguments=data.get("arguments", {}),
            )
            return json_ok({"result": result})
        except Exception as e:
            return json_error_response(
                "MCP tool call failed",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _connect(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        try:
            data = await r.json()
            client = await gw.mcp_client_manager.add_client(
                name=data["name"],
                command=data.get("command"),
                url=data.get("url"),
            )
            return json_ok(
                {
                    "name": data["name"],
                    "connected": client.connected,
                }
            )
        except KeyError as e:
            return json_error_response(
                f"Missing required field: {e}", status=400, code="validation_error"
            )
        except Exception as e:
            return json_error_response(
                "MCP connect failed",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _disconnect(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        try:
            data = await r.json()
            await gw.mcp_client_manager.remove_client(data["name"])
            return json_ok({"disconnected": True})
        except KeyError as e:
            return json_error_response(
                f"Missing required field: {e}", status=400, code="validation_error"
            )
        except Exception as e:
            return json_error_response(
                "MCP disconnect failed",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h
