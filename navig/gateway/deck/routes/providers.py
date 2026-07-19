"""
Deck API — AI provider Connections.

The deck face of the unified connection service (:mod:`navig.providers.connect`),
the same flow the CLI and onboarding use. Endpoints:

    GET    /api/deck/providers              → templates + connections + default
    POST   /api/deck/providers/connect      → create a connection (api-key/local)
    POST   /api/deck/providers/default      → set the global default
    DELETE /api/deck/providers/{id}         → safe-remove a connection

Secrets arrive only in POST bodies (loopback origin) and are never echoed back
or logged. Responses carry ``secret_ref`` labels, never secret values.
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore

from navig.providers.connect import (
    begin_oauth,
    complete_oauth,
    connect_provider,
    detect_external,
    disconnect,
    list_connections,
    list_templates,
    resolve_default,
    set_default,
    set_workspace_default,
)
from navig.providers.connection_types import (
    ConnectionError as ConnError,
)
from navig.providers.connection_types import (
    ConnectionNotFound,
    ConnectionValidationError,
)

logger = logging.getLogger(__name__)


def _template_public(t) -> dict:
    return {
        "template_id": t.template_id,
        "name": t.name,
        "driver": t.driver.value,
        "requires_key": t.requires_key,
        "requires_endpoint": t.requires_endpoint,
        "default_endpoint": t.default_endpoint,
        "auth_kind": t.auth_kind,
        "group": t.group,
        "key_placeholder": t.key_placeholder,
    }


async def handle_deck_providers_list(request: "web.Request") -> "web.Response":
    """Catalog + connections + resolved default (optionally per ?workspace=)."""
    workspace = request.query.get("workspace")
    default = resolve_default(workspace)
    return web.json_response({
        "templates": [_template_public(t) for t in list_templates()],
        "connections": list_connections(),  # includes configured-elsewhere (live)
        "detected": detect_external(),
        "default": default,
        "default_connection_id": default["connection_id"] if default else None,
    })


async def handle_deck_providers_connect(request: "web.Request") -> "web.Response":
    """Create a connection. Body: {template_id, name?, api_key?, endpoint?, model?}."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid json", "code": "bad_request"}, status=400)

    template_id = (body or {}).get("template_id")
    if not template_id:
        return web.json_response({"error": "template_id is required", "code": "bad_request"}, status=400)

    try:
        conn = await connect_provider(
            template_id,
            name=body.get("name"),
            api_key=body.get("api_key"),
            endpoint=body.get("endpoint"),
            model=body.get("model"),
        )
    except ConnectionValidationError as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=400)
    except ConnError as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=500)

    # Honest status: 200 even when needs_reauth — the connection persisted, the
    # client decides how to surface a non-routable state.
    return web.json_response({"connection": conn.to_public_dict()})


async def handle_deck_providers_oauth_start(request: "web.Request") -> "web.Response":
    """Begin an OAuth login. Body: {template_id?}. Returns {handle, auth_url}."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    template_id = (body or {}).get("template_id", "claude-max")
    try:
        return web.json_response(begin_oauth(template_id))
    except ConnectionValidationError as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=400)


async def handle_deck_providers_oauth_complete(request: "web.Request") -> "web.Response":
    """Complete an OAuth login. Body: {handle, code, name?}. Returns {connection}."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid json", "code": "bad_request"}, status=400)
    handle, code = (body or {}).get("handle"), (body or {}).get("code")
    if not handle or not code:
        return web.json_response({"error": "handle and code are required", "code": "bad_request"}, status=400)
    try:
        conn = await complete_oauth(handle, code, name=body.get("name"))
    except ConnectionValidationError as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=400)
    except Exception as exc:  # noqa: BLE001 — exchange/HTTP errors, redacted
        return web.json_response({"error": str(exc)[:300], "code": "oauth_error"}, status=502)
    return web.json_response({"connection": conn.to_public_dict()})


async def handle_deck_providers_default(request: "web.Request") -> "web.Response":
    """Set the global (or per-workspace) default. Body: {connection_id, workspace?}."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid json", "code": "bad_request"}, status=400)

    connection_id = (body or {}).get("connection_id")
    if not connection_id:
        return web.json_response({"error": "connection_id is required", "code": "bad_request"}, status=400)

    workspace = body.get("workspace")
    try:
        if workspace:
            set_workspace_default(workspace, connection_id)
        else:
            set_default(connection_id)
    except ConnectionNotFound as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=404)
    except ConnError as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=500)
    return web.json_response({"ok": True})


async def handle_deck_providers_delete(request: "web.Request") -> "web.Response":
    """Safe-remove a connection (local config + vault material only)."""
    connection_id = request.match_info.get("connection_id")
    if not connection_id:
        return web.json_response({"error": "connection_id required", "code": "bad_request"}, status=400)
    try:
        disconnect(connection_id)
    except ConnectionNotFound as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=404)
    except ConnError as exc:
        return web.json_response({"error": str(exc), "code": exc.code}, status=500)
    return web.json_response({"ok": True})
