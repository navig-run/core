"""
Social Networks settings handlers for the Deck API.

Exposes read/write access to the `telegram:` and `adapters:` config blocks
so the Deck UI can display and edit bot behaviour without touching the vault.

Routes registered in navig/gateway/deck/__init__.py:
    GET  /api/deck/social/status                 → multi-network connectivity snapshot
    GET  /api/deck/social/telegram               → full editable telegram config
    POST /api/deck/social/telegram               → partial-update telegram config
    GET  /api/deck/social/adapter/{network}      → single adapter status
    POST /api/deck/social/adapter/{network}      → toggle adapter enabled flag
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

# ── Editable telegram config keys and their expected types ───────────────────
_TELEGRAM_BOOL_KEYS = frozenset(
    {
        "require_auth",
        "reactions_enabled",
        "inline_mode_enabled",
        "checklist_enabled",
        "forum_routing_enabled",
        "auto_pin_briefings",
        "auto_pin_plans",
        # When true, the bot's reply to a short chat-feel message is
        # streamed token-by-token via editMessageText. Feels near-instant
        # because the first chunk lands within ~600ms of send. Defaults
        # to True so out-of-the-box installs get the fast UX; toggle off
        # from Settings → Social → Telegram if you prefer one-shot replies.
        "stream_replies",
    }
)
_TELEGRAM_INT_KEYS: frozenset[str] = frozenset()
_TELEGRAM_STR_KEYS = frozenset({"mode", "typing_mode"})

_TELEGRAM_ALLOWED_MODES = {"polling", "webhook"}
_TELEGRAM_ALLOWED_TYPING = {"instant", "message", "never"}

# Commands that must remain enabled — disabling them would brick the bot UI.
# Mirror this set in navig/gateway/channels/telegram_commands.py if expanded.
_TELEGRAM_LOCKED_COMMANDS: frozenset[str] = frozenset(
    {"start", "help", "settings", "status"}
)

# Supported third-party adapters
_ADAPTERS = (
    "discord", "whatsapp", "sms",
    "linkedin", "linkedin_page", "reddit",
    "instagram_meta", "instagram", "facebook",
    "threads", "youtube", "vk", "medium", "devto",
)

# Vault env-var key for each adapter's primary secret
_ADAPTER_VAULT_KEYS: dict[str, str] = {
    "discord": "DISCORD_BOT_TOKEN",
    "whatsapp": "WHATSAPP_ACCESS_TOKEN",
    "sms": "TWILIO_AUTH_TOKEN",
    "linkedin": "LINKEDIN_ACCESS_TOKEN",
    "linkedin_page": "LINKEDIN_PAGE_ACCESS_TOKEN",
    "reddit": "REDDIT_CLIENT_SECRET",
    "instagram_meta": "INSTAGRAM_META_ACCESS_TOKEN",
    "instagram": "INSTAGRAM_ACCESS_TOKEN",
    "facebook": "FACEBOOK_ACCESS_TOKEN",
    "threads": "THREADS_ACCESS_TOKEN",
    "youtube": "YOUTUBE_API_KEY",
    "vk": "VK_ACCESS_TOKEN",
    "medium": "MEDIUM_INTEGRATION_TOKEN",
    "devto": "DEVTO_API_KEY",
}

# Non-secret config keys editable per adapter (written to adapters.<network>.<key>)
_ADAPTER_STR_KEYS: dict[str, frozenset[str]] = {
    "discord": frozenset({"guild_id", "channel_id"}),
    "whatsapp": frozenset({"phone_number_id", "api_version"}),
    "sms": frozenset({"provider", "phone_number", "account_sid"}),
    "linkedin": frozenset(),
    "linkedin_page": frozenset({"organization_id"}),
    "reddit": frozenset({"client_id", "username", "subreddit"}),
    "instagram_meta": frozenset({"instagram_account_id"}),
    "instagram": frozenset(),
    "facebook": frozenset({"page_id"}),
    "threads": frozenset(),
    "youtube": frozenset({"channel_id"}),
    "vk": frozenset({"group_id"}),
    "medium": frozenset({"publication_id"}),
    "devto": frozenset({"organization_id"}),
}


def _get_config_manager():
    try:
        from navig.config import get_config_manager  # type: ignore[import]

        return get_config_manager()
    except Exception:
        return None


def _cfg_get(cfg, key: str, default=None):
    """Safely read a dot-separated key from the config manager's global_config."""
    if cfg is None:
        return default
    try:
        gcfg = cfg.global_config or {}
        parts = key.split(".")
        val: object = gcfg.get(parts[0]) if isinstance(gcfg, dict) else None
        for part in parts[1:]:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return default
        return val if val is not None else default
    except Exception:
        return default


def _cfg_set_path(cfg, key: str, value) -> None:
    """Write a dot-separated key into global_config and persist.
    Creates intermediate dicts as needed. Raises on failure."""
    if cfg is None:
        raise RuntimeError("config manager unavailable")
    gcfg = dict(cfg.global_config or {})
    parts = key.split(".")
    cursor: dict = gcfg
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
        cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value
    cfg.update_global_config(gcfg)


def _bot_connected(cfg) -> tuple[bool, str | None]:
    """
    Return (connected, masked_username).
    Checks vault for the bot token without ever returning its value.
    Username is returned as "@<name>" if the token is present; None otherwise.
    """
    try:
        from navig.vault.resolver import resolve_secret  # type: ignore[import]

        token = resolve_secret("TELEGRAM_BOT_TOKEN")
        if not token:
            return False, None
        # Derive a rough @username hint from the token prefix (not the real username —
        # a real getMe call would require an async HTTP request; keep this sync and cheap).
        return True, None
    except Exception:
        # If vault module is absent just check config for legacy key
        raw = _cfg_get(cfg, "telegram.bot_token") or _cfg_get(cfg, "gateway.bot_token")
        if raw:
            return True, None
        return False, None


# ── Handlers ─────────────────────────────────────────────────────────────────


async def handle_deck_social_status(request: "web.Request") -> "web.Response":
    """
    Multi-network connectivity snapshot.

    Response shape:
    {
        "telegram": { "connected": bool, "bot_username": str|null, "mode": str },
        "discord":  { "connected": bool, "enabled": bool },
        "whatsapp": { "connected": bool, "enabled": bool },
        "sms":      { "connected": bool, "enabled": bool }
    }
    """
    cfg = _get_config_manager()
    connected, username = _bot_connected(cfg)
    tg_mode = _cfg_get(cfg, "telegram.mode", "polling")

    result: dict[str, Any] = {
        "telegram": {
            "connected": connected,
            "bot_username": username,
            "mode": tg_mode,
        }
    }

    for network in _ADAPTERS:
        enabled = bool(_cfg_get(cfg, f"adapters.{network}.enabled", False))
        # "connected" for adapters means enabled + token present in vault
        token_key = _ADAPTER_VAULT_KEYS.get(network, "")
        net_connected = False
        if enabled and token_key:
            try:
                from navig.vault.resolver import resolve_secret  # type: ignore[import]

                net_connected = bool(resolve_secret(token_key))
            except Exception:
                pass
        result[network] = {"connected": net_connected, "enabled": enabled}

    return web.json_response(result)


async def handle_deck_social_telegram_get(request: "web.Request") -> "web.Response":
    """
    Return all editable telegram config keys.

    Response shape: flat dict of the telegram: block (no bot token).
    """
    cfg = _get_config_manager()
    tg: dict[str, Any] = {}
    if cfg:
        try:
            raw = cfg.get("telegram") or {}
            if isinstance(raw, dict):
                tg = raw
        except Exception:
            pass

    # Allowlist — never forward bot_token or other sensitive fields
    safe_keys = (
        _TELEGRAM_BOOL_KEYS | _TELEGRAM_STR_KEYS | _TELEGRAM_INT_KEYS
        | {
            "allowed_users",
            "allowed_groups",
            "typing_interval_seconds",
            "language_cache_max_age_hours",
            "disabled_commands",
            "command_styles",
        }
    )
    payload: dict[str, Any] = {k: tg[k] for k in safe_keys if k in tg}

    # Fill in defaults for keys that may be absent
    payload.setdefault("mode", "polling")
    payload.setdefault("typing_mode", "instant")
    payload.setdefault("require_auth", True)
    payload.setdefault("reactions_enabled", True)
    payload.setdefault("inline_mode_enabled", True)
    payload.setdefault("checklist_enabled", True)
    payload.setdefault("forum_routing_enabled", False)
    payload.setdefault("auto_pin_briefings", True)
    payload.setdefault("auto_pin_plans", False)
    payload.setdefault("stream_replies", True)
    payload.setdefault("allowed_users", [])
    payload.setdefault("allowed_groups", [])
    payload.setdefault("disabled_commands", [])
    payload.setdefault("command_styles", {})

    return web.json_response(payload)


async def handle_deck_social_telegram_post(request: "web.Request") -> "web.Response":
    """
    Partial-update telegram config.
    Accepts a JSON body with any subset of editable keys.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected JSON object"}, status=400)

    cfg = _get_config_manager()
    updated: list[str] = []
    errors: list[str] = []

    for key, value in body.items():
        if key in _TELEGRAM_BOOL_KEYS:
            if not isinstance(value, bool):
                errors.append(f"{key}: expected bool")
                continue
        elif key in _TELEGRAM_STR_KEYS:
            if not isinstance(value, str):
                errors.append(f"{key}: expected string")
                continue
            if key == "mode" and value not in _TELEGRAM_ALLOWED_MODES:
                errors.append(f"mode: must be one of {sorted(_TELEGRAM_ALLOWED_MODES)}")
                continue
            if key == "typing_mode" and value not in _TELEGRAM_ALLOWED_TYPING:
                errors.append(f"typing_mode: must be one of {sorted(_TELEGRAM_ALLOWED_TYPING)}")
                continue
        elif key == "allowed_users":
            if not isinstance(value, list) or not all(isinstance(x, int) for x in value):
                errors.append("allowed_users: expected list of ints")
                continue
        elif key == "allowed_groups":
            if not isinstance(value, list) or not all(isinstance(x, int) for x in value):
                errors.append("allowed_groups: expected list of ints")
                continue
        elif key == "disabled_commands":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                errors.append("disabled_commands: expected list of strings")
                continue
            normalized: list[str] = []
            seen: set[str] = set()
            locked_attempted: list[str] = []
            for raw in value:
                name = raw.lstrip("/").strip().lower()
                if not name or name in seen:
                    continue
                if name in _TELEGRAM_LOCKED_COMMANDS:
                    locked_attempted.append(name)
                    continue
                seen.add(name)
                normalized.append(name)
            if locked_attempted:
                errors.append(
                    f"disabled_commands: cannot disable locked commands: {sorted(set(locked_attempted))}"
                )
            value = normalized
        elif key == "command_styles":
            if not isinstance(value, dict):
                errors.append("command_styles: expected object {command: 'ai'|'plain'}")
                continue
            cleaned: dict[str, str] = {}
            bad: list[str] = []
            for raw_name, raw_val in value.items():
                if not isinstance(raw_name, str) or not isinstance(raw_val, str):
                    continue
                name = raw_name.lstrip("/").strip().lower()
                val = raw_val.strip().lower()
                if not name:
                    continue
                if val not in ("ai", "plain"):
                    bad.append(f"{name}={raw_val}")
                    continue
                cleaned[name] = val
            if bad:
                errors.append(f"command_styles: invalid style values: {sorted(bad)}")
            value = cleaned
        else:
            # Skip unknown / sensitive keys silently
            continue

        if cfg:
            try:
                _cfg_set_path(cfg, f"telegram.{key}", value)
                updated.append(key)
            except Exception as exc:
                errors.append(f"{key}: write error — {exc}")
        else:
            # No config manager — accept but note it wasn't persisted
            updated.append(key)

    if errors and not updated:
        return web.json_response({"ok": False, "errors": errors}, status=400)

    return web.json_response({"ok": True, "updated": updated, "errors": errors})


async def handle_deck_social_telegram_commands(request: "web.Request") -> "web.Response":
    """
    GET /api/deck/social/telegram/commands — list every available Telegram slash
    command with its category, description, locked flag, and current
    enabled/disabled state (derived from telegram.disabled_commands).
    """
    try:
        from navig.gateway.channels.telegram_commands import (  # type: ignore[import]
            _iter_unique_registry,
        )
    except Exception as exc:
        logger.warning("telegram_commands registry unavailable: %s", exc)
        return web.json_response(
            {"ok": False, "error": "command registry unavailable"}, status=500
        )

    cfg = _get_config_manager()
    raw_disabled = _cfg_get(cfg, "telegram.disabled_commands", []) or []
    disabled: set[str] = {
        str(x).lstrip("/").strip().lower() for x in raw_disabled if isinstance(x, str)
    }
    raw_styles = _cfg_get(cfg, "telegram.command_styles", {}) or {}
    styles: dict[str, str] = {}
    if isinstance(raw_styles, dict):
        for k, v in raw_styles.items():
            if isinstance(k, str) and isinstance(v, str) and v in ("ai", "plain"):
                styles[k.lstrip("/").strip().lower()] = v

    entries = []
    for entry in _iter_unique_registry(visible_only=False):
        name = entry.command.lower()
        ai_capable = bool(getattr(entry, "ai_capable", False))
        ai_default = str(getattr(entry, "ai_default", "plain") or "plain")
        if ai_default not in ("ai", "plain"):
            ai_default = "plain"
        effective_style = styles.get(name, ai_default) if ai_capable else "plain"
        entries.append({
            "name": name,
            "description": entry.description,
            "category": entry.category or "other",
            "usage": entry.usage or "",
            "visible": bool(entry.visible),
            "locked": name in _TELEGRAM_LOCKED_COMMANDS,
            "enabled": name not in disabled,
            "ai_capable": ai_capable,
            "ai_default": ai_default,
            "style": effective_style,
        })

    return web.json_response({
        "commands": entries,
        "locked": sorted(_TELEGRAM_LOCKED_COMMANDS),
        "disabled": sorted(disabled),
        "command_styles": styles,
    })


async def handle_deck_social_adapter_get(request: "web.Request") -> "web.Response":
    """Return status + non-secret config for a single adapter."""
    network = request.match_info.get("network", "")
    if network not in _ADAPTERS:
        return web.json_response(
            {"ok": False, "error": f"unknown network '{network}'"}, status=404
        )

    cfg = _get_config_manager()
    enabled = bool(_cfg_get(cfg, f"adapters.{network}.enabled", False))

# Non-secret display fields — read all _ADAPTER_STR_KEYS for this network
    extra: dict[str, Any] = {}
    for str_key in _ADAPTER_STR_KEYS.get(network, frozenset()):
        val = _cfg_get(cfg, f"adapters.{network}.{str_key}", "")
        # Partially mask phone numbers for display
        if str_key == "phone_number" and val:
            extra[str_key] = f"...{val[-4:]}"
        else:
            extra[str_key] = val or ""

    # Check whether a vault token is configured (without returning its value)
    token_configured = False
    token_key = _ADAPTER_VAULT_KEYS.get(network, "")
    if token_key:
        try:
            from navig.vault.resolver import resolve_secret  # type: ignore[import]

            token_configured = bool(resolve_secret(token_key))
        except Exception:
            pass

    return web.json_response(
        {"network": network, "enabled": enabled, "token_configured": token_configured, **extra}
    )


async def handle_deck_social_adapter_post(request: "web.Request") -> "web.Response":
    """Update a social adapter: toggle enabled flag and/or write non-secret config fields."""
    network = request.match_info.get("network", "")
    if network not in _ADAPTERS:
        return web.json_response(
            {"ok": False, "error": f"unknown network '{network}'"}, status=404
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "expected JSON object"}, status=400)

    cfg = _get_config_manager()
    updated: list[str] = []
    errors: list[str] = []

    # Handle enabled toggle
    if "enabled" in body:
        enabled = body["enabled"]
        if not isinstance(enabled, bool):
            errors.append("'enabled' must be a bool")
        else:
            if cfg:
                try:
                    _cfg_set_path(cfg, f"adapters.{network}.enabled", enabled)
                    updated.append("enabled")
                except Exception as exc:
                    errors.append(f"enabled: write error — {exc}")
            else:
                updated.append("enabled")

    # Handle non-secret string config fields
    allowed_str_keys = _ADAPTER_STR_KEYS.get(network, frozenset())
    for key, value in body.items():
        if key == "enabled":
            continue
        if key not in allowed_str_keys:
            continue  # skip unknown / sensitive keys silently
        if not isinstance(value, str):
            errors.append(f"{key}: expected string")
            continue
        if cfg:
            try:
                _cfg_set_path(cfg, f"adapters.{network}.{key}", value)
                updated.append(key)
            except Exception as exc:
                errors.append(f"{key}: write error — {exc}")
        else:
            updated.append(key)

    if errors and not updated:
        return web.json_response({"ok": False, "errors": errors}, status=400)

    current_enabled = bool(_cfg_get(cfg, f"adapters.{network}.enabled", False))
    return web.json_response(
        {"ok": True, "network": network, "enabled": current_enabled, "updated": updated, "errors": errors}
    )


# ---------------------------------------------------------------------------
# Matrix settings routes
# GET  /api/deck/social/matrix
# POST /api/deck/social/matrix
# GET  /api/deck/social/matrix/bridges
# POST /api/deck/social/matrix/bridges/deploy
# ---------------------------------------------------------------------------

_MATRIX_STR_KEYS: frozenset[str] = frozenset({"homeserver_url", "user_id", "server_name"})
_MATRIX_BOOL_KEYS: frozenset[str] = frozenset({"enabled", "e2ee"})

_BRIDGE_DEFS: list[dict] = [
    {"key": "telegram", "label": "Telegram", "emoji": "✈️"},
    {"key": "whatsapp", "label": "WhatsApp", "emoji": "🟢"},
    {"key": "messenger", "label": "Messenger", "emoji": "💬"},
]


def _matrix_connected(cfg: object | None) -> bool:
    """Return True when Matrix is enabled and a homeserver_url is configured."""
    if not _cfg_get(cfg, "matrix.enabled", False):
        return False
    url = _cfg_get(cfg, "matrix.homeserver_url", "")
    return bool(url)


async def handle_deck_social_matrix_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/social/matrix — return current Matrix settings."""
    cfg = _get_config_manager()
    return web.json_response({
        "homeserver_url": _cfg_get(cfg, "matrix.homeserver_url", ""),
        "user_id": _cfg_get(cfg, "matrix.user_id", ""),
        "server_name": _cfg_get(cfg, "matrix.server_name", ""),
        "enabled": bool(_cfg_get(cfg, "matrix.enabled", False)),
        "connected": _matrix_connected(cfg),
    })


async def handle_deck_social_matrix_update(request: "web.Request") -> "web.Response":
    """POST /api/deck/social/matrix — patch Matrix settings."""
    try:
        body: dict = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    cfg = _get_config_manager()
    updated: list[str] = []
    errors: list[str] = []

    for key, value in body.items():
        if key in _MATRIX_BOOL_KEYS:
            if not isinstance(value, bool):
                errors.append(f"{key}: expected boolean")
                continue
        elif key in _MATRIX_STR_KEYS:
            if not isinstance(value, str):
                errors.append(f"{key}: expected string")
                continue
        else:
            continue  # ignore unknown keys
        if cfg:
            try:
                _cfg_set_path(cfg, f"matrix.{key}", value)
                updated.append(key)
            except Exception as exc:
                errors.append(f"{key}: write error — {exc}")
        else:
            updated.append(key)

    if errors and not updated:
        return web.json_response({"ok": False, "errors": errors}, status=400)

    return web.json_response({
        "ok": True,
        "updated": updated,
        "errors": errors,
        "connected": _matrix_connected(_get_config_manager()),
    })


async def _async_docker_ps_filter(container_name: str) -> bool:
    """Return True when a container whose name contains *container_name* is running."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps",
            "--filter", f"name={container_name}",
            "--format", "{{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return bool(stdout.strip())
    except Exception:
        return False


async def handle_deck_social_matrix_bridges_get(request: "web.Request") -> "web.Response":
    """GET /api/deck/social/matrix/bridges — list bridge status."""
    checks = await asyncio.gather(
        *[_async_docker_ps_filter(f"mautrix-{d['key']}") for d in _BRIDGE_DEFS],
        return_exceptions=True,
    )
    bridges: list[dict] = []
    for defn, running_result in zip(_BRIDGE_DEFS, checks):
        running = running_result is True
        bridges.append({
            "key": defn["key"],
            "label": defn["label"],
            "emoji": defn["emoji"],
            "running": running,
            "linked": False,
        })
    return web.json_response({"bridges": bridges})


async def handle_deck_social_matrix_bridges_deploy(request: "web.Request") -> "web.Response":
    """POST /api/deck/social/matrix/bridges/deploy — start a bridge container."""
    try:
        body: dict = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    bridge_key = body.get("key", "")
    valid_keys = {d["key"] for d in _BRIDGE_DEFS}
    if bridge_key not in valid_keys:
        return web.json_response(
            {"ok": False, "error": f"unknown bridge key '{bridge_key}'"}, status=400
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "-d",
            "--name", f"mautrix-{bridge_key}",
            f"dock.mau.dev/mautrix/{bridge_key}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            return web.json_response({"ok": True, "bridge": bridge_key, "started": True})
        return web.json_response(
            {"ok": False, "bridge": bridge_key, "error": stderr.decode(errors="replace").strip()},
            status=500,
        )
    except FileNotFoundError:
        return web.json_response(
            {"ok": False, "error": "docker not found — install Docker to manage bridges"}, status=503
        )
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
