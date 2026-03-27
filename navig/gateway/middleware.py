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
            return web.json_response(
                {
                    "ok": False,
                    "error": "Too many failed attempts. Try again later.",
                    "error_code": "rate_limited",
                },
                status=429,
            )

        resp = await handler(request)
        auth_state[peer].append((now, resp.status == 401))
        return resp

    return rate_limit, auth_state


def make_cors_middleware() -> Any:
    """
    Return a ``@web.middleware`` coroutine that adds permissive CORS headers
    **only** on ``/deck`` and ``/api/deck`` routes (Telegram WebApp iframe).

    All other routes are passed through untouched.
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

        path = request.path
        if path.startswith("/api/deck") or path.startswith("/deck"):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, X-Telegram-Init-Data, X-Telegram-User"
            )
            resp.headers["Access-Control-Max-Age"] = "3600"
        return resp

    return cors
