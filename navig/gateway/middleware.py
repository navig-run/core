"""navig.gateway.middleware
~~~~~~~~~~~~~~~~~~~~~~~~~
Standalone aiohttp middleware factories for NavigGateway.

Extracted from server.py (Phase 2 refactor) to keep NavigGateway focused on
orchestration rather than HTTP mechanics.

Usage (in _start_http_server):
    from navig.gateway.middleware import make_rate_limit_middleware, make_cors_middleware
    rate_mw, auth_state = make_rate_limit_middleware(window=60, max_failures=5)
    cors_mw = make_cors_middleware()
    app = web.Application(middlewares=[rate_mw, cors_mw])
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from aiohttp import web

logger = logging.getLogger("navig.gateway.middleware")


def make_rate_limit_middleware(
    window: int = 60,
    max_failures: int = 5,
) -> tuple[Any, dict[str, list]]:
    """
    Return ``(middleware, auth_state)`` where:

    - *middleware* is a ``@web.middleware`` coroutine ready for
      ``web.Application(middlewares=[...])``
    - *auth_state* is the ``{ip: [(timestamp, was_failure), ...]}`` dict
      (exposed so callers can inspect or reset rate-limit state)

    The middleware blocks any IP that has accumulated *max_failures* or more
    HTTP-401 responses within the rolling *window* seconds.
    """
    auth_state: dict[str, list] = defaultdict(list)

    @web.middleware
    async def rate_limit(request: web.Request, handler: Any) -> web.Response:
        peer = request.remote or "127.0.0.1"
        now = time.monotonic()

        # Loopback peers are trusted infrastructure — never rate-limit them.
        _LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
        if peer in _LOOPBACK:
            return await handler(request)

        # Prune entries outside the rolling window
        auth_state[peer] = [(ts, failed) for ts, failed in auth_state[peer] if now - ts < window]

        recent_failures = sum(1 for _, failed in auth_state[peer] if failed)
        if recent_failures >= max_failures:
            logger.warning(
                "rate-limited %s: %d auth failures in %ds",
                peer,
                recent_failures,
                window,
            )
            headers: dict[str, str] = {}
            path = request.path
            if path.startswith("/api/deck") or path.startswith("/deck"):
                headers = {
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Telegram-Init-Data, X-Telegram-User",
                }
            return web.json_response(
                {
                    "ok": False,
                    "error": "Too many failed attempts. Try again later.",
                    "error_code": "rate_limited",
                },
                status=429,
                headers=headers or None,
            )

        resp = await handler(request)
        # Never count localhost/same-origin requests as auth failures —
        # they use the dev bypass and triggering the rate limiter produces
        # misleading noise in the log.
        origin = request.headers.get("Origin", "")
        is_local_origin = (
            origin.startswith("http://localhost:")
            or origin.startswith("http://127.0.0.1:")
        )
        is_local_peer = peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
        is_localhost = is_local_origin or (is_local_peer and not origin)
        auth_state[peer].append((now, resp.status == 401 and not is_localhost))
        return resp

    return rate_limit, auth_state


_CORS_PATH_PREFIXES = (
    "/api/deck",   # main Deck REST surface
    "/api/events", # SSE stream consumed by NavigDataProvider
    "/api/daemon", # daemon lifecycle (start/stop/status)
    "/mesh",       # Flux peer discovery
    "/runtime",    # runtime nodes / missions / receipts
    "/deck",       # legacy SPA entrypoint
)

# Origin allowlist. Wildcard CORS was a defense-in-depth gap -- the api_key
# Bearer is the real auth, but limiting the browser-callable origins blocks
# entire classes of attacks (CSRF from arbitrary pages, info leak via probing
# from random sites). Subdomains are matched suffix-style so Pages preview
# deploys (`*.navig-deck.pages.dev`) work without manual config.
_ORIGIN_EXACT = frozenset({
    "https://relay.navig.run",       # hosted Relay frontend (was deck.navig.run)
    "https://deck.navig.run",        # legacy alias — kept during transition
    "https://navig.run",
    "https://www.navig.run",
    "https://web.telegram.org",      # Telegram WebK / WebZ web clients
    "https://webk.telegram.org",
    "https://webz.telegram.org",
    "https://oauth.telegram.org",
    "http://localhost:3000",         # `npm run dev` for navig-deck
    "http://127.0.0.1:3000",
})
_ORIGIN_SUFFIX = (
    ".navig-deck.pages.dev",         # Cloudflare Pages preview deploys
    ".navig.run",                    # any future subdomain
)


def _allowed_origin(req_origin: str) -> str | None:
    if not req_origin:
        return None
    if req_origin in _ORIGIN_EXACT:
        return req_origin
    # Local dev servers on ANY port. Next/Vite pick whatever port is free
    # (3000, 7432, …), so pinning specific ports broke the dev Deck's batch
    # POST (preflight) while plain GETs slipped through. Loopback origins are
    # developer-trusted and the api_key Bearer remains the real auth, so
    # allowing any localhost/127.0.0.1 port just removes dev friction.
    if req_origin.startswith(("http://localhost:", "http://127.0.0.1:")) or req_origin in (
        "http://localhost",
        "http://127.0.0.1",
    ):
        return req_origin
    # Suffix match (must be HTTPS and end with one of the allowed suffixes)
    if req_origin.startswith("https://"):
        host_with_path = req_origin[len("https://"):]
        host = host_with_path.split("/", 1)[0]
        for suffix in _ORIGIN_SUFFIX:
            if host.endswith(suffix):
                return req_origin
    return None


def cors_headers_for(request: web.Request) -> dict[str, str]:
    """Compute the CORS headers a response on this request should carry.

    Single source of truth for both the middleware (post-handler) and any
    handler that needs to set CORS headers BEFORE response.prepare() flushes
    them to the wire (SSE / StreamResponse paths -- see
    navig/gateway/routes/events.py and navig/gateway/routes/voice.py).
    Returns {} when the request is on a non-CORS path or the origin isn't
    in the allowlist.
    """
    if not any(request.path.startswith(p) for p in _CORS_PATH_PREFIXES):
        return {}
    origin = request.headers.get("Origin", "")
    allowed = _allowed_origin(origin)
    if not allowed:
        if origin:
            logger.debug("CORS reject for origin=%r path=%s", origin, request.path)
        return {}
    return {
        "Access-Control-Allow-Origin": allowed,
        # Tell caches our response varies by Origin so we don't pollute the
        # CDN with a single permissive copy.
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers":
            "Content-Type, Authorization, X-Telegram-Init-Data, X-Telegram-User",
        "Access-Control-Allow-Credentials": "false",
        "Access-Control-Max-Age": "3600",
    }


def make_cors_middleware() -> Any:
    """Add CORS headers on browser-callable routes, allowlisted by origin.

    The daemon is reached from the hosted Relay (relay.navig.run; legacy
    deck.navig.run still allowed) and the Telegram WebView
    (web.telegram.org / Telegram desktop). Other origins
    are explicitly NOT allowed -- the api_key Bearer is the real auth, but
    restricting origins blocks CSRF from arbitrary pages and removes
    information leakage from random sites probing for endpoints.

    NOTE: this middleware adds headers AFTER the handler returns, which is
    fine for plain `web.Response` but TOO LATE for `web.StreamResponse`
    (where prepare() flushes headers before the handler returns). Streaming
    handlers must call `cors_headers_for(request)` themselves and pass the
    result into the StreamResponse constructor BEFORE prepare().
    """

    @web.middleware
    async def cors(request: web.Request, handler: Any) -> web.Response:
        if request.method == "OPTIONS":
            resp: web.Response = web.Response(status=200)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as exc:
                resp = exc  # type: ignore[assignment]

        headers = cors_headers_for(request)
        for k, v in headers.items():
            resp.headers[k] = v
        return resp

    return cors
