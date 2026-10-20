"""Connector management endpoints for the Deck API.

Exposes the live ConnectorRegistry and ConnectorAuthManager to the
navig-deck frontend so users can browse, connect, and disconnect
OAuth-based integrations from the UI.

Endpoints:
  GET  /api/deck/connectors              — list all connectors with live status
  GET  /api/deck/connectors/catalog      — the connector catalog (filter + page)
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
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _get_registry_and_auth():
    from navig.connectors.auth_manager import ConnectorAuthManager
    from navig.connectors.bootstrap import ensure_connectors_loaded
    from navig.connectors.registry import get_connector_registry

    ensure_connectors_loaded()
    return get_connector_registry(), ConnectorAuthManager()


def _build_connector_list(registry, auth_mgr) -> list[dict]:
    """Build enriched connector list with live status + connected account.

    Uses a single vault read for all connected accounts (O(1) lookups) rather
    than 2 vault reads per connector.
    """
    try:
        accounts = auth_mgr.list_connected_accounts()  # {connector_id: email|""}
    except Exception as exc:
        logger.debug("list_connected_accounts failed: %s", exc)
        accounts = {}

    items = []
    for info in registry.list_all():
        cid = info["id"]
        cls = registry.all_classes().get(cid)
        manifest = getattr(cls, "manifest", None)

        connected = cid in accounts
        account = accounts.get(cid) or None
        if manifest is not None and getattr(manifest, "auth_mode", "") == "api_key":
            # A key connector has no vault OAuth token; it is connected when its key resolves.
            from navig.gateway.deck.routes.services import key_fields_connected

            connected = key_fields_connected(manifest)

        items.append({
            "id": cid,
            "display_name": info.get("display_name", cid),
            "description": getattr(manifest, "description", "") if manifest else "",
            "domain": info.get("domain", "other"),
            "icon": info.get("icon", "🔗"),
            "status": "connected" if connected else info.get("status", "disconnected"),
            "tier": "native",
            "requires_oauth": getattr(manifest, "requires_oauth", True) if manifest else True,
            "auth_mode": getattr(manifest, "auth_mode", "oauth") if manifest else "oauth",
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


def _callback_html(title: str, message: str, ok: bool) -> str:
    color = "#10b981" if ok else "#ef4444"
    icon = "✓" if ok else "✕"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#0a0a0d; color:#e5e5e5; display:flex; align-items:center;
         justify-content:center; height:100vh; }}
  .card {{ text-align:center; padding:2.5rem 2rem; }}
  .icon {{ font-size:3rem; color:{color}; }}
  h1 {{ font-size:1.25rem; margin:1rem 0 0.5rem; }}
  p  {{ color:#a1a1aa; font-size:0.9rem; max-width:320px; }}
</style></head><body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
  <script>setTimeout(function(){{ try {{ window.close(); }} catch(e){{}} }}, 2500);</script>
</body></html>"""


async def handle_deck_connectors_oauth_callback(request: "web.Request") -> "web.Response":
    """GET /api/deck/connectors/oauth/callback?code=&state=

    The redirect target for the headless OAuth flow. The OAuth provider sends
    the browser here after the user authorizes. We exchange the code for tokens
    (stored in the vault) and return a small self-closing success page.

    This route is allow-listed past deck auth (the provider redirect carries no
    bearer token) — security comes from the unguessable PKCE `state` token,
    which must match a pending in-memory entry created by /connect.
    """
    code = request.query.get("code", "").strip()
    state = request.query.get("state", "").strip()
    error = request.query.get("error", "").strip()

    if error:
        return web.Response(
            text=_callback_html("Authorization Declined", f"The provider returned: {error}", ok=False),
            content_type="text/html",
            status=400,
        )
    if not code or not state:
        return web.Response(
            text=_callback_html("Invalid Callback", "Missing authorization code or state.", ok=False),
            content_type="text/html",
            status=400,
        )

    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        creds = await ConnectorAuthManager().exchange_auth_code(state, code)
        account = creds.email or "your account"
        return web.Response(
            text=_callback_html("Connected", f"Linked {account}. You can close this window.", ok=True),
            content_type="text/html",
        )
    except Exception as exc:
        logger.error("OAuth redirect callback error: %s", exc)
        return web.Response(
            text=_callback_html("Connection Failed", str(exc), ok=False),
            content_type="text/html",
            status=400,
        )


async def handle_deck_connectors_disconnect(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/connectors/{connector_id}[?account=<email>] — remove one linked
    account (the default one when none is given)."""
    connector_id = request.match_info.get("connector_id", "")
    if not connector_id:
        return web.json_response({"ok": False, "error": "connector_id required"}, status=400)

    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        from navig.connectors.errors import ConnectorAuthError
        from navig.connectors.registry import get_connector_registry

        auth = ConnectorAuthManager()
        account = (request.query.get("account") or "").strip() or None
        try:
            removed = await auth.revoke(connector_id, account)
        except ConnectorAuthError as exc:  # no such linked account
            return web.json_response({"ok": False, "error": str(exc)}, status=404)

        # Mark the instance disconnected only when no account is left (removing the
        # default promotes the next linked account).
        registry = get_connector_registry()
        inst = registry.peek_instance(connector_id)
        if inst and not auth.list_accounts(connector_id):
            from navig.connectors.types import ConnectorStatus
            inst.status = ConnectorStatus.DISCONNECTED

        return web.json_response({"ok": True, "connector_id": connector_id, "removed": removed})
    except Exception as exc:
        logger.error("Disconnect error for %s: %s", connector_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_connectors_health(request: "web.Request") -> "web.Response":
    """GET /api/deck/connectors/{connector_id}/health"""
    connector_id = request.match_info.get("connector_id", "")
    if not connector_id:
        return web.json_response({"ok": False, "error": "connector_id required"}, status=400)

    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        from navig.connectors.bootstrap import ensure_connectors_loaded
        from navig.connectors.errors import ConnectorNotFoundError
        from navig.connectors.registry import get_connector_registry

        ensure_connectors_loaded()
        registry = get_connector_registry()

        try:
            connector = registry.get(connector_id)
        except ConnectorNotFoundError:
            return web.json_response({"ok": False, "error": f"Connector '{connector_id}' not found"}, status=404)

        # Hydrate the instance with its vault token before probing. If there's
        # no stored token, report "not connected" rather than a raw token error.
        injected = await ConnectorAuthManager().inject_token(connector)
        if not injected and getattr(connector.manifest, "requires_oauth", True):
            return web.json_response({"ok": False, "latency_ms": 0, "message": "Not connected"})
        if not getattr(connector.manifest, "requires_oauth", True):
            # Key connectors load their key in connect(); without it health_check() can
            # only ever answer "Not connected".
            try:
                await connector.connect()
            except Exception as exc:  # noqa: BLE001 — the reason IS the health answer
                return web.json_response({"ok": False, "latency_ms": 0, "message": str(exc)})

        # Return health fields at the top level. NB: HealthStatus.to_dict()
        # carries its own `ok` (the probe result) — that IS the body's `ok`.
        health = await connector.health_check()
        return web.json_response(health.to_dict())
    except Exception as exc:
        logger.error("Health check error for %s: %s", connector_id, exc)
        return web.json_response({"ok": False, "message": str(exc)}, status=500)


# ── Drive a connector (search / fetch / act) — powers in-app UIs ────────────

async def _resolve_connector(connector_id: str):
    """Load a connector and hydrate its vault token. Returns (connector, None)
    or (None, web.Response) on error/not-connected."""
    from navig.connectors.auth_manager import ConnectorAuthManager
    from navig.connectors.bootstrap import ensure_connectors_loaded
    from navig.connectors.errors import ConnectorNotFoundError
    from navig.connectors.registry import get_connector_registry

    ensure_connectors_loaded()
    try:
        obj = get_connector_registry().get(connector_id)
    except ConnectorNotFoundError:
        return None, web.json_response({"ok": False, "error": f"Connector '{connector_id}' not found"}, status=404)
    connector = obj() if isinstance(obj, type) else obj
    if connector is None:
        return None, web.json_response({"ok": False, "error": "connector unavailable"}, status=500)
    if getattr(connector.manifest, "requires_oauth", True):
        if not await ConnectorAuthManager().inject_token(connector):
            return None, web.json_response({"ok": False, "error": "Not connected"}, status=402)
    else:
        try:
            await connector.connect()  # key connectors load their key here
        except Exception as exc:  # noqa: BLE001
            return None, web.json_response({"ok": False, "error": str(exc)}, status=402)
    return connector, None


_CATALOG_MAX_LIMIT = 500


async def handle_deck_connectors_catalog(request: "web.Request") -> "web.Response":
    """``GET /api/deck/connectors/catalog`` — the connector catalog, filtered and paged.

    Query: ``category`` ``country`` ``region`` ``tier`` ``status`` ``integration`` ``batch``
    ``q`` (name/vendor substring), ``limit`` (default 100, max 500), ``offset``.
    NAVIG OS asks ``integration=remote_mcp`` for the services it can add in one click.
    Answers ``{ok, data: {total, offset, limit, entries, categories, summary}}`` —
    ``summary`` covers the WHOLE catalog, so a filtered view can still show the totals.
    """
    import asyncio

    from navig.connectors import catalog

    q = request.query
    try:
        batch = int(q["batch"]) if q.get("batch") else None
        limit = min(max(int(q.get("limit", 100)), 1), _CATALOG_MAX_LIMIT)
        offset = max(int(q.get("offset", 0)), 0)
    except ValueError:
        return web.json_response(
            {"ok": False, "error": "batch, limit and offset must be integers"}, status=400
        )
    for key, allowed in (
        ("category", catalog.CATEGORY_IDS),
        ("tier", catalog.TIERS),
        ("status", catalog.STATUSES),
        ("region", catalog.REGION_IDS),
        ("integration", catalog.INTEGRATIONS),
    ):
        if q.get(key) and q[key] not in allowed:
            return web.json_response(
                {"ok": False, "error": f"unknown {key} {q[key]!r}", "allowed": sorted(allowed)},
                status=400,
            )
    try:
        # First load parses ~5k rows (~0.2 s): keep it off the event loop.
        await asyncio.to_thread(catalog.load_catalog)
        rows = catalog.query(
            category=q.get("category") or None,
            country=q.get("country") or None,
            region=q.get("region") or None,
            tier=q.get("tier") or None,
            status=q.get("status") or None,
            batch=batch,
            text=q.get("q") or None,
            integration=q.get("integration") or None,
        )
    except catalog.CatalogError as exc:
        logger.error("connector catalog is malformed: %s", exc)
        return web.json_response({"ok": False, "error": f"catalog is malformed: {exc}"}, status=500)
    return web.json_response({
        "ok": True,
        "data": {
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "entries": [e.to_dict() for e in rows[offset:offset + limit]],
            "categories": [
                {"id": c.id, "label": c.label, "icon": c.icon} for c in catalog.CATEGORIES
            ],
            "summary": catalog.summary(),
        },
    })


async def handle_deck_connectors_search(request: "web.Request") -> "web.Response":
    connector_id = request.match_info.get("connector_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = str(body.get("query", "")).strip()
    try:
        limit = int(body.get("limit", 20) or 20)
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "limit must be an integer"}, status=400)
    try:
        connector, err = await _resolve_connector(connector_id)
        if err is not None:
            return err
        results = await connector.search(query, limit=limit)
        return web.json_response({"ok": True, "data": {"results": [r.to_dict() for r in results]}})
    except Exception as exc:
        logger.exception("connector search failed for %s", connector_id)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_connectors_fetch(request: "web.Request") -> "web.Response":
    connector_id = request.match_info.get("connector_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    resource_id = str(body.get("resource_id", "")).strip()
    if not resource_id:
        return web.json_response({"ok": False, "error": "resource_id required"}, status=400)
    try:
        connector, err = await _resolve_connector(connector_id)
        if err is not None:
            return err
        res = await connector.fetch(resource_id)
        return web.json_response({"ok": True, "data": {"resource": res.to_dict() if res else None}})
    except Exception as exc:
        logger.exception("connector fetch failed for %s", connector_id)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_connectors_act(request: "web.Request") -> "web.Response":
    connector_id = request.match_info.get("connector_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from navig.connectors.types import Action, ActionType

        try:
            action_type = ActionType(str(body.get("action_type", "")))
        except ValueError:
            return web.json_response(
                {"ok": False, "error": f"unknown action_type; valid: {[a.value for a in ActionType]}"},
                status=400,
            )
        connector, err = await _resolve_connector(connector_id)
        if err is not None:
            return err
        action = Action(
            action_type=action_type,
            connector_id=connector_id,
            resource_id=body.get("resource_id"),
            params=body.get("params") or {},
        )
        result = await connector.act(action)
        return web.json_response({"ok": True, "data": result.to_dict()})
    except Exception as exc:
        logger.exception("connector act failed for %s", connector_id)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


# ── MCP server management (Tier 3) ──────────────────────────────────────────

def _get_config_manager(request=None):
    try:
        from navig.config import get_config_manager
        return get_config_manager()
    except Exception:
        return None


def live_mcp_manager(request) -> Any:
    """The running gateway's ``MCPClientManager``, or ``None`` (CLI, tests, boot race)."""
    try:
        gateway = request.app.get("gateway") if hasattr(request, "app") else None
    except Exception:  # noqa: BLE001 — a request without an app is simply "no gateway"
        gateway = None
    return getattr(gateway, "mcp_client_manager", None) if gateway is not None else None


async def apply_mcp_entry_live(request, name: str, entry: dict | None) -> str:
    """Make the running gateway match one ``mcp.servers`` entry, without a restart.

    Add/replace/delete used to touch config.yaml only: a new server did nothing until
    the next restart, and a DELETED one kept running (and kept serving tools) until
    then too. ``entry=None`` means removed. Returns what happened, for the response —
    ``"applied"`` · ``"removed"`` · ``"disabled"`` · ``"no_gateway"`` (config saved; it
    applies at next boot) · ``"error: …"``. Never raises: the config write already
    succeeded, and a live-apply failure must not report the save as failed.
    """
    mgr = live_mcp_manager(request)
    if mgr is None:
        return "no_gateway"
    try:
        await mgr.remove_client(name)
        if entry is None:
            return "removed"
        from navig.mcp import MCPClientConfig

        cfg = MCPClientConfig.from_server_entry(entry)
        if not cfg.enabled:
            return "disabled"
        await mgr.add_client(cfg)
        return "applied"
    except Exception as exc:  # noqa: BLE001 — reported in the response, see docstring
        logger.warning("MCP live apply failed for %s: %s", name, exc)
        return f"error: {exc}"


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

    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "body must be a JSON object"}, status=400)
    raw_name, raw_type = body.get("name", ""), body.get("type", "")
    if not isinstance(raw_name, str) or not isinstance(raw_type, str):
        # `.strip()` on a non-string raised outside any handler — a 500 for bad input.
        return web.json_response({"ok": False, "error": "name and type must be strings"}, status=400)
    name = raw_name.strip()
    server_type = raw_type.strip()
    for key, kind in (("args", list), ("env", dict), ("headers", dict)):
        if body.get(key) is not None and not isinstance(body.get(key), kind):
            return web.json_response(
                {"ok": False, "error": f"{key} must be a {kind.__name__}"}, status=400
            )

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

    # Ask BEFORE writing. A stdio entry here is "run this binary as me" — and unlike
    # /mcp/connect, which adds a runtime client, this one lands in config.yaml and the
    # gateway auto-connects everything under `mcp.servers` at boot, so an unapproved
    # command would run again on every restart. Same decision, one implementation.
    from navig.mcp.registration import authorize_registration

    refused = await authorize_registration(
        name=name,
        command=body.get("command"),
        args=body.get("args"),
        url=body.get("url"),
        transport="stdio" if server_type == "stdio" else "http",
    )
    if refused is not None:
        return web.json_response(
            {"ok": False, "error": refused.detail, "code": refused.code},
            status=403,
        )

    try:
        from navig.config import get_config_manager
        cfg = get_config_manager()
        # `global_config` is a process-lifetime cache, so a read-modify-write built on it
        # can persist a stale snapshot and drop a setting written meanwhile (by the CLI,
        # or another route). `refresh_global_config()` re-reads, and `set_global` is the
        # one safe dotted write — refresh, deep-set the leaf, save.
        servers = list((cfg.refresh_global_config().get("mcp") or {}).get("servers", []))

        # Replace if name already exists
        servers = [s for s in servers if s.get("name") != name]
        servers.append(server_config)
        cfg.set_global("mcp.servers", servers)

        live = await apply_mcp_entry_live(request, name, server_config)
        return web.json_response({"ok": True, "server": server_config, "live": live})
    except Exception as exc:
        logger.error("MCP server add error: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_deck_mcp_remove(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/mcp/servers/{name}"""
    name = request.match_info.get("name", "")
    if not name:
        return web.json_response({"ok": False, "error": "name required"}, status=400)

    try:
        from navig.config import get_config_manager
        cfg = get_config_manager()
        # Same discipline as the add path: refresh before read-modify-write, and write
        # the one leaf rather than replacing the whole `mcp` subtree.
        current = (cfg.refresh_global_config().get("mcp") or {}).get("servers", [])
        cfg.set_global(
            "mcp.servers", [s for s in current if s.get("name") != name]
        )
        live = await apply_mcp_entry_live(request, name, None)
        return web.json_response({"ok": True, "removed": name, "live": live})
    except Exception as exc:
        logger.error("MCP server remove error: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
