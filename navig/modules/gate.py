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


def _module_gate_unavailable_response(module_id: str) -> "web.Response":
    """The gate could not DECIDE — distinct from a policy denial.

    503 rather than 403 so a surface can tell "you turned this off" from "the registry
    is broken". Collapsing them would make a registry fault look like a user choice,
    and a surface that auto-enables on 403 would then flip a module on behind the
    operator's back.
    """
    return web.json_response(
        {"ok": False, "error": "module_gate_unavailable", "module": module_id},
        status=503,
    )


def requires_module(module_id: str) -> Callable[[Handler], Handler]:
    """Decorator: gate an aiohttp handler on a module being ENABLED.

    On disabled: HTTP 403 + ``module_disabled``. On enabled: the handler runs.
    On a registry error: **fails closed** with HTTP 503 + ``module_gate_unavailable``.

    Failing closed is the whole point of a gate. The earlier behaviour — log a
    warning and let the request through — meant any exception in the registry
    silently un-gated every capability route in the daemon, which is the failure
    mode a gate exists to prevent.

    Recovery is not cut off: this decorator is applied only to *capability* routes
    (see the ``@requires_module`` call sites under ``gateway/deck`` and the plugins'
    ``deck_routes``). Liveness, readiness and auth-bootstrap routes are never
    decorated, so a broken registry degrades exactly the surface it is supposed to
    guard and nothing else.

    The ``try`` covers only the registry lookup, deliberately. The handler runs
    outside it: previously it was inside, so a handler that raised was caught,
    logged as "allowing through", and **invoked a second time** — every side effect
    of that handler executed twice.
    """

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapped(request: "web.Request") -> "web.Response":
            try:
                from navig.modules.registry import get_registry

                enabled = get_registry().is_enabled(module_id)
            except Exception as exc:  # noqa: BLE001 — a broken registry must not decide
                logger.error(
                    "module gate for %r could not decide (%s: %s); failing closed",
                    module_id, type(exc).__name__, exc,
                )
                return _module_gate_unavailable_response(module_id)
            if not enabled:
                return _module_disabled_response(module_id)
            return await handler(request)

        # Tag for introspection (parallels requires_capability's tag).
        wrapped.__navig_required_module__ = module_id  # type: ignore[attr-defined]
        return wrapped

    return decorator
