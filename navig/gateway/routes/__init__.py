"""
Gateway HTTP route modules.

Each module provides a ``register(app, gateway)`` function
that adds its routes to the aiohttp Application.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web
    from navig.gateway.server import NavigGateway


def register_all_routes(app: "web.Application", gateway: "NavigGateway") -> None:
    """Register every route group on *app*."""
    from navig.gateway.routes import (
        core,
        heartbeat,
        cron,
        approval,
        browser,
        llm,
        mcp,
        tasks,
        memory,
        proactive,
        telegram_webhook,
        router_status,
        mesh,
        runtime,
        daemon,
        install,
        audit,
        billing,
    )

    for mod in (core, heartbeat, cron, approval, browser, llm, mcp, tasks, memory, proactive, telegram_webhook, router_status, mesh, runtime, daemon, install, audit, billing):
        mod.register(app, gateway)
