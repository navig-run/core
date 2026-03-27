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
}


def configure_deck_auth(
    bot_token: str,
    allowed_users: list[int],
    require_auth: bool = True,
    dev_mode: bool = False,
    auth_max_age: int = 3600,
) -> None:
    """Set the module-level auth config for Deck API."""
    _deck_config["bot_token"] = bot_token
    _deck_config["allowed_users"] = set(allowed_users) if allowed_users else set()
    _deck_config["require_auth"] = require_auth
    _deck_config["dev_mode"] = dev_mode
    _deck_config["auth_max_age"] = auth_max_age
    logger.info(
        "Deck auth configured: require_auth=%s, allowed_users=%d, dev_mode=%s",
        require_auth,
        len(_deck_config["allowed_users"]),
        dev_mode,
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


def _get_user_id(request: "web.Request", bot_token: str = "") -> int | None:
    token = bot_token or _deck_config["bot_token"]
    max_age = _deck_config["auth_max_age"]

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data and token:
        result = validate_init_data(init_data, token, max_age)
        if result and result.get("user"):
            return result["user"]["id"]

    if _deck_config["dev_mode"]:
        user_header = request.headers.get("X-Telegram-User", "")
        if user_header.isdigit():
            return int(user_header)

    return None


if web:

    @web.middleware
    async def deck_auth_middleware(request: "web.Request", handler):
        path = request.path

        if not path.startswith("/api/deck"):
            return await handler(request)

        if request.method == "OPTIONS":
            return await handler(request)

        user_id = _get_user_id(request)

        if user_id is None:
            logger.warning("Deck API unauthorized: no valid user from %s %s", request.method, path)
            return web.json_response(
                {
                    "error": "unauthorized",
                    "detail": "Valid Telegram WebApp initData required",
                },
                status=401,
            )

        allowed = _deck_config["allowed_users"]
        require_auth = _deck_config["require_auth"]
        if require_auth and allowed and user_id not in allowed:
            logger.warning("Deck API forbidden: user %d not in allowed_users", user_id)
            return web.json_response(
                {"error": "forbidden", "detail": "User not authorized for Deck"},
                status=403,
            )

        request["deck_user_id"] = user_id
        return await handler(request)
