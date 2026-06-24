"""
Deck API Authentication

Middleware and utilities for verifying Telegram WebApp initData.
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qs, unquote

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

_deck_config: dict[str, Any] = {
    "bot_token": "",
    "allowed_users": set(),
    "require_auth": True,
    "dev_mode": False,
    "auth_max_age": 86400,
    "api_key": "",  # Bearer token accepted from desktop browser
}


def configure_deck_auth(
    bot_token: str,
    allowed_users: list[int],
    require_auth: bool = True,
    dev_mode: bool = False,
    auth_max_age: int = 3600,
    api_key: str = "",
) -> None:
    """Set the module-level auth config for Deck API."""
    _deck_config["bot_token"] = bot_token
    _deck_config["allowed_users"] = set(allowed_users) if allowed_users else set()
    _deck_config["require_auth"] = require_auth
    _deck_config["dev_mode"] = dev_mode
    _deck_config["auth_max_age"] = auth_max_age
    _deck_config["api_key"] = api_key or ""
    logger.info(
        "Deck auth configured: require_auth=%s, allowed_users=%d, dev_mode=%s, api_key=%s",
        require_auth,
        len(_deck_config["allowed_users"]),
        dev_mode,
        "set" if api_key else "not set",
    )


def validate_init_data(
    init_data: str, bot_token: str, max_age: int = 3600
) -> dict[str, Any] | None:
    if not init_data or not bot_token:
        return None

    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            return None

        items = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            items.append(f"{key}={unquote(values[0])}")
        items.sort()
        data_check_string = "\n".join(items)

        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        auth_date = int(parsed.get("auth_date", [0])[0])
        if time.time() - auth_date > max_age:
            return None

        user_str = parsed.get("user", [None])[0]
        user = json.loads(unquote(user_str)) if user_str else None

        return {
            "user": user,
            "auth_date": auth_date,
            "valid": True,
        }
    except Exception as e:
        logger.debug("initData validation failed: %s", e)
        return None


_DEV_BYPASS_SENTINEL = -1  # Negative = dev/localhost bypass; skips allowlist


def _get_user_id(request: "web.Request", bot_token: str = "") -> int | None:
    """Return the authenticated user_id, or None if not authenticated.

    Returns _DEV_BYPASS_SENTINEL (negative) for dev/localhost bypass requests
    so the auth middleware knows to skip the allowlist check.
    """
    token = bot_token or _deck_config["bot_token"]
    max_age = _deck_config["auth_max_age"]

    # Bearer token: accepted from desktop browser (api_key set in deck config)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:].strip()
        configured_key = _deck_config.get("api_key", "")
        if bearer and configured_key and bearer == configured_key:
            logger.debug("Deck API: valid Bearer token (api_key match)")
            return _DEV_BYPASS_SENTINEL

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data and token:
        result = validate_init_data(init_data, token, max_age)
        if result and result.get("user"):
            return result["user"]["id"]

    if _deck_config["dev_mode"]:
        user_header = request.headers.get("X-Telegram-User", "")
        if user_header.isdigit():
            return int(user_header)
        # dev_mode with no X-Telegram-User header → deny (return None → 401).
        # The loopback bypass below is intentionally skipped in dev_mode.

    # Auto-bypass for requests originating from localhost — production only.
    # In dev_mode the caller must supply X-Telegram-User (handled above).
    # Covers two cases:
    #   a) Cross-origin requests from the React dev server (Origin: http://localhost:*)
    #   b) Same-origin requests from the SPA served at 127.0.0.1:8765 (no Origin header,
    #      request.remote == '127.0.0.1')  ← the common production case
    if not _deck_config["dev_mode"]:
        origin = request.headers.get("Origin", "")
        if origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
            logger.debug("Deck API: localhost origin %s → dev bypass", origin)
            return _DEV_BYPASS_SENTINEL
        remote = getattr(request, "remote", "") or ""
        _LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
        if not origin and remote in _LOOPBACK:
            logger.debug("Deck API: same-origin request from %s → dev bypass", remote)
            return _DEV_BYPASS_SENTINEL

    return None


if web:

    # Paths under /api/deck that bypass auth. The OAuth provider redirect lands
    # here with no bearer token; security is the unguessable PKCE `state` token
    # (validated against an in-memory pending entry created by /connect).
    _PUBLIC_DECK_PATHS = frozenset({
        "/api/deck/connectors/oauth/callback",
    })

    @web.middleware
    async def deck_auth_middleware(request: "web.Request", handler):
        path = request.path

        if not path.startswith("/api/deck"):
            return await handler(request)

        if request.method == "OPTIONS":
            return await handler(request)

        if path in _PUBLIC_DECK_PATHS:
            return await handler(request)

        user_id = _get_user_id(request)

        _CORS_HEADERS = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Telegram-Init-Data, X-Telegram-User",
        }

        if user_id is None:
            logger.warning(
                "Deck API unauthorized: no valid auth from %s %s (origin=%s)",
                request.method,
                path,
                request.headers.get("Origin", "-"),
            )
            return web.json_response(
                {
                    "error": "unauthorized",
                    "detail": "Valid Telegram WebApp initData required",
                },
                status=401,
                headers=_CORS_HEADERS,
            )

        allowed = _deck_config["allowed_users"]
        require_auth = _deck_config["require_auth"]
        # Bypass: dev/localhost sentinel — skip the allowlist check entirely
        if user_id != _DEV_BYPASS_SENTINEL and require_auth and allowed and user_id not in allowed:
            logger.warning("Deck API forbidden: user %d not in allowed_users", user_id)
            return web.json_response(
                {"error": "forbidden", "detail": "User not authorized for Deck"},
                status=403,
                headers=_CORS_HEADERS,
            )

        request["deck_user_id"] = abs(user_id)  # store positive ID for handlers
        return await handler(request)
