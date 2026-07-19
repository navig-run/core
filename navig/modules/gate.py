"""
Module enable/disable gating for daemon HTTP routes.

FREE modules (no license capability) are toggled purely via the module
registry's user override (``modules.overrides`` — see ``modules/registry.py``).
This wraps an aiohttp handler so a *disabled* module's routes return HTTP 403 +
a structured ``module_disabled`` error, letting a surface degrade gracefully
instead of hitting a dead route.

This is the free-tier analogue of ``navig.license.gate.requires_capability``
(which gates PAID capabilities and returns 402). Use ``requires_module`` for
always-available modules the user can switch off; use ``requires_capability``
for licensed ones.

Usage::

    from navig.modules.gate import requires_module

    @requires_module("media")
    async def handle_studio_networks(request: web.Request) -> web.Response:
        ...

Toggling is LIVE (no restart): ``is_enabled`` re-reads the config override on
every call, so flipping ``modules.overrides`` takes effect on the next request.
"""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

Handler = Callable[["web.Request"], Awaitable["web.Response"]]


def _module_disabled_response(module_id: str) -> "web.Response":
    return web.json_response(
        {"ok": False, "error": "module_disabled", "module": module_id},
        status=403,
    )


def requires_module(module_id: str) -> Callable[[Handler], Handler]:
    """Decorator: gate an aiohttp handler on a module being ENABLED.

    On disabled: returns HTTP 403 + structured payload. On enabled: calls the
    handler normally. Registry errors degrade OPEN (the handler runs) — never
    crash a request because the module registry misbehaves.
    """

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapped(request: "web.Request") -> "web.Response":
            try:
                from navig.modules.registry import get_registry

                if get_registry().is_enabled(module_id):
                    return await handler(request)
                return _module_disabled_response(module_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "module gate for %r raised %r; allowing through", module_id, exc
                )
                return await handler(request)

        # Tag for introspection (parallels requires_capability's tag).
        wrapped.__navig_required_module__ = module_id  # type: ignore[attr-defined]
        return wrapped

    return decorator
