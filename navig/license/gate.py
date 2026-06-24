"""
Module capability gating for daemon HTTP routes.

Wraps an aiohttp handler so it returns HTTP 402 + a structured
``capability_required`` error if the currently-installed license does not
include the named module. The Deck reads the response shape uniformly via
``lib/api.ts`` and re-renders ModuleLockedCard inline (defence in depth on
top of the tab-level UI gate).

Usage:

    from navig.license.gate import requires_capability

    @requires_capability("business_ops")
    async def handle_bizops_overview(request: web.Request) -> web.Response:
        ...

The wrapped handler is called normally on license OK; on lock the
response is:

    HTTP 402 Payment Required
    {
      "ok": false,
      "error": "capability_required",
      "capability": "business_ops",
      "tier_required": "pro",
      "current_tier": "solo"
    }

The Deck transforms this into a ModuleLockedCard render rather than
crashing the section.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

from navig.license.quota import TIER_CAPABILITIES, TierName

logger = logging.getLogger(__name__)

Handler = Callable[["web.Request"], Awaitable["web.Response"]]


# Smallest tier that includes each module. Used to populate ``tier_required``
# in the 402 payload so the Deck can render the upgrade CTA without a
# round-trip.
_SMALLEST_TIER_FOR_MODULE: dict[str, TierName] = {
    "core_ops": "solo",
    "business_ops": "pro",
    "ai_operator": "pro",
    "security_ops": "business",
    "deploy_ops": "business",
    "client_ops": "fleet",
}


def _capability_required_response(
    capability: str,
    current_tier: str,
) -> "web.Response":
    return web.json_response(
        {
            "ok": False,
            "error": "capability_required",
            "capability": capability,
            "tier_required": _SMALLEST_TIER_FOR_MODULE.get(capability, "pro"),
            "current_tier": current_tier,
        },
        status=402,
    )


def requires_capability(module: str) -> Callable[[Handler], Handler]:
    """Decorator: gate an aiohttp handler on a license module capability.

    On lock: returns HTTP 402 + structured payload. On ok: calls the
    handler normally. License-module errors degrade gracefully -- if the
    license subsystem raises, the handler runs (don't crash a request
    because licensing misbehaves).
    """

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapped(request: "web.Request") -> "web.Response":
            try:
                from navig.license import current_status
                status = current_status()
                caps = list(status.capabilities or [])
                if module in caps:
                    return await handler(request)
                return _capability_required_response(
                    capability=module,
                    current_tier=status.effective_tier or "solo",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "license gate for %r raised %r; allowing through", module, exc
                )
                return await handler(request)

        # Tag the handler so register code can introspect what's gated --
        # useful for the future /api/deck/_meta/modules endpoint.
        wrapped.__navig_required_capability__ = module  # type: ignore[attr-defined]
        return wrapped

    return decorator
