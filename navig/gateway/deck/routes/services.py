"""One list of every service NAVIG can connect, and a working action for each.

Before this route the app's "Connect your services" showed the 12 brain connectors only,
and six of them could not connect at all: the five API-key connectors answered 404 on
Connect (there is no OAuth flow for a key) and Google Calendar had no OAuth loader in the
deck path. An OAuth connector whose app credentials were unset also 404'd, with no way to
say what was missing. Publishing networks and messaging channels were not listed.

Endpoints:
  GET    /api/deck/services                         every service, with status + action
  POST   /api/deck/services/{service_id}/credentials save a key, or an OAuth app's client id
  DELETE /api/deck/services/{service_id}/credentials forget a saved key (disconnect)

A service row:
  {
    "id": "connector:gmail",            # <source>:<key> — unique across sources
    "name", "description", "icon", "category", "source",
    "status": "connected" | "available" | "needs_setup",
    "accounts": [{"id", "label", "status", "remove"?}],   # remove: DELETE url for one account
    "multi_account": bool,
    "connect": {
      "kind": "oauth" | "api_key" | "settings" | "external",
      "ready": bool,                    # false → fill `fields` first (or read `reason`)
      "reason": str,                    # why it is not ready, in plain words
      "fields": [KeyField.to_dict()],   # what to ask for; POSTed to `save`
      "redirect_uri": str,              # register this in the provider's OAuth app
      "start": url, "save": url, "remove": url,  # remove is a DELETE
      "paste": bool, "reply": url,      # a flow that needs the redirect URL pasted back
      "settings": "telegram" | "messaging",
      "last_error": str,
    },
  }
Plugins add rows through :mod:`navig.gateway.deck.service_sources`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Longest value accepted for one field — a GCP service-account JSON is ~2.4 KB.
_MAX_FIELD_LEN = 64 * 1024

CATEGORIES = ("email", "productivity", "developer", "data", "social", "messaging", "finance")

_DOMAIN_CATEGORY = {
    "calendar": "email",
    "communication": "productivity",
    "file_storage": "productivity",
    "project_management": "productivity",
    "knowledge": "productivity",
    "events": "productivity",
    "dev": "developer",
    "data": "data",
    "ai_research": "data",
    "payments": "finance",
    "crm": "finance",
    "marketing": "social",
}
_ID_CATEGORY = {"gmail": "email", "youtube": "data", "supabase": "developer"}

_MESSAGING = (
    ("telegram", "Telegram", "Your bot — chat with the Operator from anywhere.", "✈️", "telegram"),
    ("discord", "Discord", "Post to a Discord server channel.", "🎮", "messaging"),
    ("whatsapp", "WhatsApp", "Send messages through the WhatsApp Business API.", "💬", "messaging"),
    ("sms", "SMS", "Text messages through Twilio.", "📱", "messaging"),
)


# ── Brain connectors ─────────────────────────────────────────────────────────


def _connector_category(cid: str, domain: str) -> str:
    return _ID_CATEGORY.get(cid) or _DOMAIN_CATEGORY.get(domain, "productivity")


def key_fields_connected(manifest) -> bool:
    """An API-key connector is connected when every required field resolves."""
    from navig.connectors import secrets

    fields = tuple(getattr(manifest, "key_fields", ()) or ())
    return bool(fields) and not secrets.missing(fields)


def _connector_row(
    cid: str, cls, accounts: dict[str, str], providers_ready: set[str], linked: list[str] | None = None
) -> dict[str, Any]:
    from navig.connectors import secrets
    from navig.connectors.oauth_redirect import connector_redirect_uri

    m = cls.manifest
    domain = getattr(getattr(m, "domain", None), "value", "") or str(getattr(m, "domain", ""))
    auth_mode = getattr(m, "auth_mode", "oauth")
    base = f"/api/deck/services/connector:{cid}/credentials"
    row: dict[str, Any] = {
        "id": f"connector:{cid}",
        "name": m.display_name,
        "description": m.description,
        "icon": m.icon,
        "category": _connector_category(cid, domain),
        "source": "connector",
        "accounts": [],
        "multi_account": False,
    }

    if auth_mode == "api_key":
        fields = tuple(m.key_fields)
        connected = not secrets.missing(fields)
        row["accounts"] = [{"id": "default", "label": "API key saved", "status": "ok"}] if connected else []
        row["status"] = "connected" if connected else "available"
        row["connect"] = {
            "kind": "api_key",
            "ready": True,
            "fields": [f.to_dict() for f in fields],
            "save": base,
            "remove": base,
        }
        return row

    if auth_mode == "manual_credentials":
        connected = cid in accounts
        row["accounts"] = [{"id": "default", "label": "Configured", "status": "ok"}] if connected else []
        row["status"] = "connected" if connected else "available"
        row["connect"] = {
            "kind": "external",
            "ready": False,
            "reason": "Set up from the Deck's Connectors section.",
        }
        return row

    connected = cid in accounts
    if connected:
        # Every linked account, each removable on its own; the default one first.
        emails = [e for e in (linked or []) if e and e != "(default)"] or [accounts.get(cid) or ""]
        row["accounts"] = [
            {
                "id": email or "default",
                "label": email or "Connected",
                "status": "ok",
                "remove": f"/api/deck/connectors/{cid}" + (f"?account={quote(email)}" if email else ""),
            }
            for email in emails
        ]
    ready = cid in providers_ready
    row["multi_account"] = True  # OAuth: linking another account adds it beside the first
    connect: dict[str, Any] = {
        "kind": "oauth",
        "ready": ready,
        "start": f"/api/deck/connectors/{cid}/connect",
        "remove": f"/api/deck/connectors/{cid}",
        "redirect_uri": connector_redirect_uri(),
    }
    oauth_app = getattr(m, "oauth_app", None)
    if not ready:
        if oauth_app:
            ns, prefix, label = oauth_app
            connect["fields"] = [f.to_dict() for f in secrets.oauth_app_fields(ns, prefix, label)]
            connect["save"] = base
            connect["reason"] = f"Needs your {label} client ID and secret first."
        else:
            connect["reason"] = "This connector has no sign-in configured."
    row["connect"] = connect
    row["status"] = "connected" if connected else ("available" if ready else "needs_setup")
    return row


def _connector_rows() -> list[dict[str, Any]]:
    from navig.connectors.auth_manager import ConnectorAuthManager
    from navig.connectors.bootstrap import ensure_connectors_loaded
    from navig.connectors.registry import get_connector_registry

    ensure_connectors_loaded()
    registry = get_connector_registry()
    try:
        accounts = ConnectorAuthManager().list_connected_accounts()
    except Exception as exc:  # noqa: BLE001 — a locked vault lists everything as not connected
        logger.debug("list_connected_accounts failed: %s", exc)
        accounts = {}
    classes = registry.all_classes()
    providers_ready = {cid for cid in classes if ConnectorAuthManager.get_provider_config(cid)}
    auth = ConnectorAuthManager()
    rows = []
    for cid, cls in classes.items():
        try:
            linked = auth.list_accounts(cid) if cid in accounts else []
            rows.append(_connector_row(cid, cls, accounts, providers_ready, linked))
        except Exception as exc:  # noqa: BLE001 — one broken manifest must not hide the rest
            logger.debug("service row for %s failed: %s", cid, exc)
    return rows


# ── Messaging ────────────────────────────────────────────────────────────────


def _messaging_rows() -> list[dict[str, Any]]:
    from navig.gateway.deck.routes.social import social_status_snapshot

    snap = social_status_snapshot()
    rows = []
    for key, name, desc, icon, settings in _MESSAGING:
        state = snap.get(key) or {}
        connected = bool(state.get("connected"))
        label = "Bot token set" if key == "telegram" else "Enabled"
        rows.append({
            "id": f"messaging:{key}",
            "name": name,
            "description": desc,
            "icon": icon,
            "category": "messaging",
            "source": "messaging",
            "status": "connected" if connected else "available",
            "accounts": [{"id": "default", "label": label, "status": "ok"}] if connected else [],
            "multi_account": False,
            "connect": {"kind": "settings", "ready": True, "settings": settings},
        })
    return rows


# ── List ─────────────────────────────────────────────────────────────────────


def build_services() -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Every row from every source, plus ``{source: "ok" | error}``. Never raises."""
    from navig.gateway.deck.service_sources import service_sources

    rows: list[dict[str, Any]] = []
    status: dict[str, str] = {}
    producers = {"connector": _connector_rows, "messaging": _messaging_rows, **service_sources()}
    for name, produce in producers.items():
        try:
            rows.extend(produce())
            status[name] = "ok"
        except Exception as exc:  # noqa: BLE001 — a failing source is reported, not fatal
            logger.warning("services source %s failed: %s", name, exc)
            status[name] = str(exc) or type(exc).__name__
    seen: set[str] = set()
    unique = []
    for row in rows:
        if row.get("id") in seen:
            continue
        seen.add(row["id"])
        unique.append(row)
    return unique, status


async def handle_deck_services_list(request: "web.Request") -> "web.Response":
    """GET /api/deck/services"""
    rows, sources = await asyncio.to_thread(build_services)
    return web.json_response({"ok": True, "services": rows, "sources": sources, "categories": list(CATEGORIES)})


# ── Credentials ──────────────────────────────────────────────────────────────


def _connector_class(service_id: str):
    from navig.connectors.bootstrap import ensure_connectors_loaded
    from navig.connectors.registry import get_connector_registry

    source, _, cid = service_id.partition(":")
    if source != "connector" or not cid:
        return None, cid
    ensure_connectors_loaded()
    return get_connector_registry().all_classes().get(cid), cid


def _fields_for(cls) -> tuple[list, str]:
    """(fields, kind) the credentials route accepts for *cls* — kind is api_key | oauth_app."""
    from navig.connectors import secrets

    m = cls.manifest
    if getattr(m, "auth_mode", "oauth") == "api_key":
        return list(m.key_fields), "api_key"
    if getattr(m, "oauth_app", None):
        ns, prefix, label = m.oauth_app
        return list(secrets.oauth_app_fields(ns, prefix, label)), "oauth_app"
    return [], ""


async def _probe(cls) -> tuple[bool, str]:
    """Connect + health-check a fresh instance of an API-key connector."""
    inst = cls()
    try:
        await inst.connect()
        health = await inst.health_check()
        return bool(health.ok), str(getattr(health, "message", "") or "")
    except Exception as exc:  # noqa: BLE001 — the message is what the operator needs to see
        return False, str(exc)
    finally:
        try:
            await inst.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _save(cls, values: dict[str, str]) -> dict[str, Any]:
    """Store *values*, verify, and roll back on a failed check. Runs in a worker thread."""
    from navig.connectors import secrets

    fields, kind = _fields_for(cls)
    by_name = {f.name: f for f in fields}
    previous = {name: secrets.vault_secret(f.vault_label) for name, f in by_name.items() if name in values}
    for name, value in values.items():
        if value:
            secrets.store(by_name[name], value)
        elif by_name[name].optional:
            secrets.forget(by_name[name])

    if kind == "oauth_app":
        from navig.connectors.bootstrap import register_oauth_providers

        register_oauth_providers()
        return {"ok": True, "verified": False}

    ok, message = asyncio.run(_probe(cls))
    if not ok:
        for name, old in previous.items():
            if old:
                secrets.store(by_name[name], old)
            else:
                secrets.forget(by_name[name])
        return {"ok": False, "error": message or "The service rejected this key."}
    return {"ok": True, "verified": True, "message": message}


async def handle_deck_services_credentials_save(request: "web.Request") -> "web.Response":
    """POST /api/deck/services/{service_id}/credentials — body ``{"values": {name: value}}``."""
    service_id = request.match_info.get("service_id", "")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
    values = body.get("values") if isinstance(body, dict) else None
    if not isinstance(values, dict) or not values:
        return web.json_response({"ok": False, "error": "values required"}, status=400)

    cls, cid = await asyncio.to_thread(_connector_class, service_id)
    if cls is None:
        return web.json_response({"ok": False, "error": f"unknown service '{service_id}'"}, status=404)
    fields, kind = _fields_for(cls)
    if not fields:
        return web.json_response({"ok": False, "error": f"{cid} takes no saved credentials"}, status=400)

    by_name = {f.name: f for f in fields}
    clean: dict[str, str] = {}
    for name, value in values.items():
        if name not in by_name:
            return web.json_response({"ok": False, "error": f"unknown field '{name}'"}, status=400)
        if not isinstance(value, str) or len(value) > _MAX_FIELD_LEN:
            return web.json_response({"ok": False, "error": f"'{name}' must be text"}, status=400)
        clean[name] = value.strip()
    if kind == "oauth_app":
        if not clean.get("client_id"):
            return web.json_response({"ok": False, "error": "client_id is required"}, status=400)
    else:
        from navig.connectors import secrets

        blank = [
            f.name for f in fields
            if not f.optional and not clean.get(f.name) and not secrets.resolve(f)
        ]
        if blank:
            return web.json_response({"ok": False, "error": "missing: " + ", ".join(blank)}, status=400)

    try:
        result = await asyncio.to_thread(_save, cls, clean)
    except Exception as exc:  # noqa: BLE001 — a locked vault is the usual cause
        logger.error("saving credentials for %s failed: %s", service_id, exc)
        return web.json_response({"ok": False, "error": f"Could not save to the vault: {exc}"}, status=500)
    return web.json_response(result, status=200 if result.get("ok") else 422)


async def handle_deck_services_credentials_delete(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/services/{service_id}/credentials — forget an API-key connector's key."""
    from navig.connectors import secrets

    service_id = request.match_info.get("service_id", "")
    cls, cid = await asyncio.to_thread(_connector_class, service_id)
    if cls is None:
        return web.json_response({"ok": False, "error": f"unknown service '{service_id}'"}, status=404)
    if getattr(cls.manifest, "auth_mode", "") != "api_key":
        return web.json_response({"ok": False, "error": "use the service's own disconnect"}, status=400)
    removed = await asyncio.to_thread(lambda: [f.name for f in cls.manifest.key_fields if secrets.forget(f)])
    still = await asyncio.to_thread(key_fields_connected, cls.manifest)
    return web.json_response({
        "ok": True,
        "removed": removed,
        # A key supplied by an environment variable cannot be removed from here.
        "still_connected": still,
    })
