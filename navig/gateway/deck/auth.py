"""
Deck API Authentication

Middleware and utilities for verifying Telegram WebApp initData.
"""

import hashlib
import hmac
import ipaddress
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

# Coalesce the very chatty "local request → desktop bypass" debug line. Every
# Deck SPA poll hits auth, so logging each one floods --debug. Instead we count
# them and emit a single summary line at most once per window.
_LOCAL_BYPASS_WINDOW = 30.0  # seconds
_local_bypass_state: dict[str, float | int] = {"count": 0, "last_flush": 0.0}


def _log_local_bypass(origin: str) -> None:
    """Emit the desktop-bypass debug line at most once per window, with a count.

    First hit in a window logs immediately (so it's visible); subsequent hits
    are tallied and folded into the next window's summary as "(xN in last 30s)".
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    now = time.monotonic()
    state = _local_bypass_state
    state["count"] = int(state["count"]) + 1
    if now - float(state["last_flush"]) >= _LOCAL_BYPASS_WINDOW:
        count = int(state["count"])
        suffix = f" (x{count} in last {int(_LOCAL_BYPASS_WINDOW)}s)" if count > 1 else ""
        logger.debug("Deck API: local request (origin=%r) → desktop bypass%s", origin, suffix)
        state["count"] = 0
        state["last_flush"] = now


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

_LOOPBACK_REMOTES = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _forwarded_client_ip(request: "web.Request") -> str | None:
    """Real client IP when the request was relayed by a proxy/tunnel, else None.

    Cloudflare (edge + cloudflared tunnel) stamps the genuine remote address in
    CF-Connecting-IP / True-Client-IP and the client cannot override it. A
    generic/local reverse proxy (incl. the Next.js dev-server rewrite) uses
    X-Forwarded-For. None means no forwarding headers → a direct connection.
    """
    for h in ("CF-Connecting-IP", "True-Client-IP"):
        v = request.headers.get(h, "").strip()
        if v:
            return v
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()  # first hop = original client
    return None


def _request_is_local(request: "web.Request") -> bool:
    """True only if the request genuinely originates from this machine.

    Accepts a direct loopback connection AND a *local* reverse proxy / Next dev
    rewrite (whose forwarded client IP is itself loopback). Rejects anything that
    arrived via the cloudflared tunnel: Cloudflare always stamps CF-Ray plus a
    public CF-Connecting-IP that the caller cannot strip, so tunneled traffic can
    never masquerade as local — even though cloudflared connects to the daemon
    from 127.0.0.1.
    """
    # CF-Ray is only ever present on Cloudflare edge/tunnel traffic.
    if request.headers.get("CF-Ray"):
        return False
    fwd = _forwarded_client_ip(request)
    if fwd is not None:
        try:
            return ipaddress.ip_address(fwd).is_loopback
        except ValueError:
            return False
    remote = getattr(request, "remote", "") or ""
    return remote in _LOOPBACK_REMOTES


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
        # Constant-time compare to avoid leaking the api_key byte-by-byte via
        # response timing (matches routes/common.py:require_bearer_auth).
        if bearer and configured_key and hmac.compare_digest(bearer, configured_key):
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

    # Auto-bypass for requests that are genuinely local to this machine — the
    # desktop user talking to their own daemon. Covers two cases:
    #   a) Cross-origin requests from the React dev server (Origin: http://localhost:*)
    #   b) Same-origin requests from the SPA served at 127.0.0.1:8789 (no Origin
    #      header, request.remote == '127.0.0.1')
    #
    # SECURITY: this must NEVER fire for tunneled traffic. cloudflared forwards
    # internet requests to the daemon from 127.0.0.1, so request.remote is
    # loopback for them too — loopback alone is not proof of local origin. We
    # therefore refuse the bypass whenever edge/proxy headers are present, which
    # forces tunneled callers to present a real Bearer api_key or Telegram
    # initData. Local desktop requests carry none of these headers, so the
    # desktop experience is unaffected.
    if not _deck_config["dev_mode"] and _request_is_local(request):
        # _request_is_local() has already excluded tunneled traffic, so any
        # genuinely-local request is trusted here: a direct same-origin call from
        # the desktop SPA (no Origin), a cross-origin call from the React dev
        # server (Origin: http://localhost:7432), or that same call relayed by
        # the Next dev-rewrite proxy (X-Forwarded-For: 127.0.0.1).
        origin = request.headers.get("Origin", "")
        if (
            not origin
            or origin.startswith("http://localhost:")
            or origin.startswith("http://127.0.0.1:")
        ):
            _log_local_bypass(origin)
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
