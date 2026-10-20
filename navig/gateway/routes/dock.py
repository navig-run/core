"""Dock bridge, agent side: drive the operator's browser through the paired NAVIG Dock.

    GET  /dock/status                                   → bridge state + ping latency
    POST /dock/call    {tool, args, deadline_ms, actor} → {ok, data, timing} | {ok:false, error}
    POST /dock/cancel  {id}

Bearer-only (the gateway admin token the CLI and ``navig mcp serve`` already send) — an
extension origin or a web page can never reach these. Tools and their arguments are
defined once in ``navig.dock.protocol``.
"""

from __future__ import annotations

from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth

_MAX_BODY = 256 * 1024


def register(app, gateway):
    app.router.add_get("/dock/status", _status(gateway))
    app.router.add_post("/dock/call", _call(gateway))
    app.router.add_post("/dock/cancel", _cancel(gateway))


def _status(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        from navig.dock.hub import get_bridge  # noqa: PLC0415

        return json_ok(get_bridge().status())

    return h


async def _body(r) -> dict | None:
    if (r.content_length or 0) > _MAX_BODY:
        return None
    try:
        data = await r.json()
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _call(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        from navig.dock.hub import get_bridge  # noqa: PLC0415
        from navig.dock.protocol import DockCallError  # noqa: PLC0415

        data = await _body(r)
        if data is None:
            return json_error_response("Body must be a JSON object under 256 KB", status=400,
                                       code="validation_error")
        try:
            out = await get_bridge().call(str(data.get("tool") or ""), data.get("args"),
                                          deadline=data.get("deadline_ms"),
                                          actor=str(data.get("actor") or ""), cid=data.get("id"),
                                          session=str(data.get("session") or ""))
        except DockCallError as exc:
            # 200 with ok:false — a browser refusal is a normal tool outcome, not an HTTP fault.
            return json_ok({"ok": False, "error": exc.to_dict()})
        return json_ok({"ok": True, **out})

    return h


def _cancel(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        from navig.dock.hub import get_bridge  # noqa: PLC0415

        data = await _body(r) or {}
        return json_ok({"cancelled": await get_bridge().cancel(str(data.get("id") or ""))})

    return h
