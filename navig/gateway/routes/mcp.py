"""MCP routes: /mcp/clients, tools, tools/{name}/call, connect, disconnect."""

from __future__ import annotations

try:
    from aiohttp import web  # noqa: F401
except ImportError as _exc:
    raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from _exc
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
        if err is not None:
            return err
        clients = []
        for name, client in gw.mcp_client_manager.clients.items():
            # MCPClient exposes `is_connected` (transport up AND handshake done).
            # There is no `.connected` — reading it raised AttributeError, so this
            # endpoint returned an unhandled 500 on every call.
            clients.append({"name": name, "connected": client.is_connected})
        return json_ok({"clients": clients})

    return h


def _tools(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err is not None:
            return err
        # The manager's method is `get_all_tools()` — `list_tools()` does not exist,
        # and MCPTool carries `server_id`, not `client_name`. Both raised
        # AttributeError, so this endpoint 500'd on every call.
        tools = gw.mcp_client_manager.get_all_tools()
        return json_ok(
            {
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "client": t.server_id,
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
        if err is not None:
            return err
        try:
            tool_name = r.match_info["tool_name"]
            data = await r.json()
            # The manager's parameter is `name`, not `tool_name` — the old keyword
            # raised TypeError, so every tool call failed with a 500.
            result = await gw.mcp_client_manager.call_tool(
                tool_name,
                data.get("arguments", {}),
            )
            return json_ok({"result": result})
        except PermissionError as e:
            # A refused approval is the operator's answer, not a server fault. Reporting
            # it as a 500 would read as "NAVIG broke" and invite a retry.
            return json_error_response(
                "MCP tool call not approved",
                status=403,
                code="approval_denied",
                details={"error": str(e)},
            )
        except Exception as e:
            return json_error_response(
                "MCP tool call failed",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


async def _authorize_registration(
    data: dict, command, url, transport: str
):
    """Render :func:`navig.mcp.registration.authorize_registration` as a 403, or ``None``.

    The decision itself lives in ``navig.mcp.registration`` because this is not the only
    door: ``POST /api/deck/mcp/servers`` registers the same capability and *persists* it
    to ``config.yaml``, which the gateway auto-connects at boot. This function is only the
    HTTP rendering — the two routes speak different error envelopes, so each keeps its own.

    Registering a **stdio** server means "run this binary, with these arguments, as me,
    and keep it running" — strictly more powerful than ``bash_exec``, which the agent
    cannot invoke without asking. This route asked nobody.

    It matters because bearer auth is not the boundary it looks like:
    ``require_bearer_auth`` returns None — open access — when ``gateway.auth.token`` is
    unset, and there is no default. The gateway binds to 127.0.0.1, so the exposure is
    every local process rather than the network, which is still an escalation: a program
    with no rights to the operator's vault or hosts can obtain them by POSTing a command
    here.

    Returns ``None`` when the registration may proceed.
    """
    from navig.mcp.registration import authorize_registration

    refused = await authorize_registration(
        name=str(data.get("name", "")),
        command=command,
        args=data.get("args", []),
        url=url,
        transport=transport,
    )
    if refused is None:
        return None
    return json_error_response(
        "MCP server registration not approved",
        status=403,
        code=refused.code,
        details={"error": refused.detail},
    )


def _connect(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err is not None:
            return err
        try:
            from navig.mcp.client import MCPClientConfig

            data = await r.json()
            # add_client() takes ONE MCPClientConfig — the old name=/command=/url=
            # keywords raised TypeError, so every connect failed with a 500.
            command = data.get("command")
            url = data.get("url")
            # transport defaults to "stdio"; a url-only request means an HTTP server,
            # which would otherwise build a stdio transport with no command.
            transport = data.get("transport") or ("sse" if url and not command else "stdio")

            denial = await _authorize_registration(data, command, url, transport)
            if denial is not None:
                return denial

            client = await gw.mcp_client_manager.add_client(
                MCPClientConfig(
                    id=data["name"],
                    command=command,
                    args=data.get("args", []),
                    url=url,
                    transport=transport,
                )
            )
            return json_ok(
                {
                    "name": data["name"],
                    # add_client connects in the BACKGROUND, so this is usually still
                    # False right here — poll GET /mcp/clients for the settled state.
                    "connected": client.is_connected,
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
        if err is not None:
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
