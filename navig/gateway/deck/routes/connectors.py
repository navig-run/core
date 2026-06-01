"""Connector management endpoints for the Deck API.

Exposes the live ConnectorRegistry and ConnectorAuthManager to the
navig-deck frontend so users can browse, connect, and disconnect
OAuth-based integrations from the UI.

Endpoints:
  GET  /api/deck/connectors              — list all connectors with live status
  POST /api/deck/connectors/{id}/connect — get OAuth auth URL
  POST /api/deck/connectors/{id}/connect/callback — exchange code for token
  DELETE /api/deck/connectors/{id}       — disconnect (revoke vault entry)
  GET  /api/deck/connectors/{id}/health  — health check

MCP server management (Tier 3 — custom servers):
  GET    /api/deck/mcp/servers           — list configured MCP servers
  POST   /api/deck/mcp/servers           — add a new MCP server config
  DELETE /api/deck/mcp/servers/{name}    — remove a MCP server config
"""

from __future__ import annotations

import logging

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _get_registry_and_auth():
    from navig.connectors.bootstrap import ensure_connectors_loaded
    from navig.connectors.registry import get_connector_registry
    from navig.connectors.auth_manager import ConnectorAuthManager

    ensure_connectors_loaded()
    return get_connector_registry(), ConnectorAuthManager()


def _build_connector_list(registry, auth_mgr) -> list[dict]:
    """Build enriched connector list with live status + connected account."""
    items = []
    for info in registry.list_all():
        cid = info["id"]
        cls = registry.all_classes().get(cid)
        manifest = getattr(cls, "manifest", None)

        connected = auth_mgr.is_connected(cid)
        account = auth_mgr.get_connected_account(cid) if connected else None

        items.append({
            "id": cid,
            "display_name": info.get("display_name", cid),
            "description": getattr(manifest, "description", "") if manifest else "",
            "domain": info.get("domain", "other"),
            "icon": info.get("icon", "🔗"),
            "status": "connected" if connected else info.get("status", "disconnected"),
            "tier": "native",
            "requires_oauth": getattr(manifest, "requires_oauth", True) if manifest else True,
            "can_search": info.get("can_search", False),
            "can_fetch": info.get("can_fetch", False),
            "can_act": info.get("can_act", False),
            "connected_account": account,
        })

    return items


async def handle_deck_connectors_list(request: "web.Request") -> "web.Response":
    """GET /api/deck/connectors — live connector catalog with status."""
    try:
        registry, auth_mgr = _get_registry_and_auth()
        items = _build_connector_list(registry, auth_mgr)

        # Also return MCP servers (Tier 3)
        mcp_servers = _load_mcp_servers(request)

        return web.json_response({"ok": True, "connectors": items, "mcp_servers": mcp_servers})
    except Exception as exc:
        logger.error("Connector list error: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_connectors_connect(request: "web.Request") -> "web.Response":
    """POST /api/deck/connectors/{connector_id}/connect

    Returns an OAuth authorization URL for the client to open in a browser.
    Also returns the PKCE state token needed for the subsequent callback.
    """
    connector_id = request.match_info.get("connector_id", "")
    if not connector_id:
        return web.json_response({"ok": False, "error": "connector_id required"}, status=400)

    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        from navig.connectors.errors import ConnectorNotFoundError

        auth_mgr = ConnectorAuthManager()
        try:
            auth_url, state, _verifier = auth_mgr.get_auth_url(connector_id)
        except ConnectorNotFoundError:
            return web.json_response(
                {"ok": False, "error": f"Connector '{connector_id}' not found or not configured"},
                status=404,
            )

        return web.json_response({
            "ok": True,
            "connector_id": connector_id,
            "auth_url": auth_url,
            "state": state,
        })
    except Exception as exc:
        logger.error("Connector connect error for %s: %s", connector_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_connectors_callback(request: "web.Request") -> "web.Response":
    """POST /api/deck/connectors/{connector_id}/connect/callback

    Exchange OAuth authorization code for tokens.
    Body: { "state": "...", "code": "..." }
    """
    connector_id = request.match_info.get("connector_id", "")

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    state = body.get("state", "").strip()
    code = body.get("code", "").strip()

    if not state or not code:
        return web.json_response({"ok": False, "error": "state and code required"}, status=400)

    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        from navig.connectors.errors import ConnectorAuthError

        auth_mgr = ConnectorAuthManager()
        try:
            creds = await auth_mgr.exchange_auth_code(state, code)
        except ConnectorAuthError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        return web.json_response({
            "ok": True,
            "connector_id": connector_id,
            "connected_account": creds.email,
        })
    except Exception as exc:
        logger.error("OAuth callback error for %s: %s", connector_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_connectors_disconnect(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/connectors/{connector_id} — revoke + disconnect."""
    connector_id = request.match_info.get("connector_id", "")
    if not connector_id:
        return web.json_response({"ok": False, "error": "connector_id required"}, status=400)

    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        from navig.connectors.registry import get_connector_registry

        await ConnectorAuthManager().revoke(connector_id)

        # Also mark instance as disconnected if it exists
        registry = get_connector_registry()
        if registry.has(connector_id):
            inst = registry._instances.get(connector_id)
            if inst:
                from navig.connectors.types import ConnectorStatus
                inst.status = ConnectorStatus.DISCONNECTED

        return web.json_response({"ok": True, "connector_id": connector_id})
    except Exception as exc:
        logger.error("Disconnect error for %s: %s", connector_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_connectors_health(request: "web.Request") -> "web.Response":
    """GET /api/deck/connectors/{connector_id}/health"""
    connector_id = request.match_info.get("connector_id", "")
    if not connector_id:
        return web.json_response({"ok": False, "error": "connector_id required"}, status=400)

    try:
        from navig.connectors.errors import ConnectorNotFoundError
        from navig.connectors.registry import get_connector_registry
        from navig.connectors.bootstrap import ensure_connectors_loaded

        ensure_connectors_loaded()
        registry = get_connector_registry()

        try:
            connector = registry.get(connector_id)
        except ConnectorNotFoundError:
            return web.json_response({"ok": False, "error": f"Connector '{connector_id}' not found"}, status=404)

        health = await connector.health_check()
        return web.json_response({"ok": True, **health.to_dict()})
    except Exception as exc:
        logger.error("Health check error for %s: %s", connector_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


# ── MCP server management (Tier 3) ──────────────────────────────────────────

def _get_config_manager(request=None):
    try:
        from navig.core.shared_config import get_config_manager
        return get_config_manager()
    except Exception:
        return None


def _load_mcp_servers(request=None) -> list[dict]:
    """Load custom MCP server configs from navig config."""
    try:
        cfg = _get_config_manager(request)
        if not cfg:
            return []
        raw = cfg.global_config or {}
        return list(raw.get("mcp", {}).get("servers", []))
    except Exception as exc:
        logger.debug("Failed to load MCP servers: %s", exc)
        return []


async def handle_deck_mcp_list(request: "web.Request") -> "web.Response":
    """GET /api/deck/mcp/servers"""
    return web.json_response({"ok": True, "servers": _load_mcp_servers(request)})


async def handle_deck_mcp_add(request: "web.Request") -> "web.Response":
    """POST /api/deck/mcp/servers — register a new MCP server config.

    Body (same format as Claude Code's .mcp.json):
      { "name": "my-api", "type": "http", "url": "https://..." }
      { "name": "local-tool", "type": "stdio", "command": "/path/to/bin", "args": [] }
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    name = body.get("name", "").strip()
    server_type = body.get("type", "").strip()

    if not name:
        return web.json_response({"ok": False, "error": "name is required"}, status=400)
    if server_type not in ("http", "stdio"):
        return web.json_response({"ok": False, "error": "type must be 'http' or 'stdio'"}, status=400)
    if server_type == "http" and not body.get("url"):
        return web.json_response({"ok": False, "error": "url is required for http type"}, status=400)
    if server_type == "stdio" and not body.get("command"):
        return web.json_response({"ok": False, "error": "command is required for stdio type"}, status=400)

    server_config = {
        "name": name,
        "type": server_type,
    }
    if server_type == "http":
        server_config["url"] = body["url"]
        if body.get("headers"):
            server_config["headers"] = body["headers"]
    else:
        server_config["command"] = body["command"]
        if body.get("args"):
            server_config["args"] = body["args"]
        if body.get("env"):
            server_config["env"] = body["env"]

    try:
        from navig.core.shared_config import get_config_manager
        cfg = get_config_manager()
        raw = dict(cfg.global_config or {})
        mcp = dict(raw.get("mcp", {}))
        servers = list(mcp.get("servers", []))

        # Replace if name already exists
        servers = [s for s in servers if s.get("name") != name]
        servers.append(server_config)
        mcp["servers"] = servers
        raw["mcp"] = mcp
        cfg.update_global_config({"mcp": mcp})

        return web.json_response({"ok": True, "server": server_config})
    except Exception as exc:
        logger.error("MCP server add error: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_mcp_remove(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/mcp/servers/{name}"""
    name = request.match_info.get("name", "")
    if not name:
        return web.json_response({"ok": False, "error": "name required"}, status=400)

    try:
        from navig.core.shared_config import get_config_manager
        cfg = get_config_manager()
        raw = dict(cfg.global_config or {})
        mcp = dict(raw.get("mcp", {}))
        servers = [s for s in mcp.get("servers", []) if s.get("name") != name]
        mcp["servers"] = servers
        cfg.update_global_config({"mcp": mcp})
        return web.json_response({"ok": True, "removed": name})
    except Exception as exc:
        logger.error("MCP server remove error: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
