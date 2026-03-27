"""
Router status HTTP route for the NAVIG gateway.

Exposes ``/router/status`` returning the unified router's health,
available providers, active provider, and recent trace summaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway

logger = logging.getLogger(__name__)


async def _router_status(request: "web.Request") -> "web.Response":
    """GET /router/status — provider availability, health, active provider."""
    from aiohttp import web as _web

    try:
        from navig.routing.router import get_router

        router = get_router()
        status = await router.status()
        return _web.json_response(status.to_dict())
    except Exception as e:
        logger.error("Router status failed: %s", e)
        return _web.json_response(
            {"error": str(e), "providers": []},
            status=500,
        )


async def _router_traces(request: "web.Request") -> "web.Response":
    """GET /router/traces — recent route traces (JSONL)."""
    from aiohttp import web as _web

    try:
        from navig.routing.trace import recent_traces

        limit = int(request.query.get("limit", "50"))
        traces = recent_traces(limit=limit)
        return _web.json_response({"traces": traces, "count": len(traces)})
    except Exception as e:
        logger.error("Router traces failed: %s", e)
        return _web.json_response({"error": str(e), "traces": []}, status=500)


async def _router_detect(request: "web.Request") -> "web.Response":
    """POST /router/detect — classify a message without executing."""
    from aiohttp import web as _web

    try:
        body = await request.json()
        text = body.get("text", "")
        if not text:
            return _web.json_response({"error": "text required"}, status=400)

        from navig.routing.detect import detect_mode

        mode, confidence, reasons = detect_mode(text)

        from navig.routing.capabilities import MODE_CAPABILITIES

        caps = MODE_CAPABILITIES.get(mode)

        return _web.json_response(
            {
                "mode": mode,
                "confidence": confidence,
                "reasons": reasons,
                "capabilities": {
                    "required": list(caps.required) if caps else [],
                    "preferred": list(caps.preferred) if caps else [],
                    "cost_target": caps.cost_target if caps else "",
                    "latency_target": caps.latency_target if caps else "",
                },
            }
        )
    except Exception as e:
        return _web.json_response({"error": str(e)}, status=500)


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register router status routes."""
    app.router.add_get("/router/status", _router_status)
    app.router.add_get("/router/traces", _router_traces)
    app.router.add_post("/router/detect", _router_detect)
