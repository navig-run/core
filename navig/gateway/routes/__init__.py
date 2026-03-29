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


def register_all_routes(app: web.Application, gateway: NavigGateway) -> None:
    """Register every route group on *app*."""
    from navig.gateway.routes import (
        approval,
        audit,
        billing,
        browser,
        core,
        cron,
        daemon,
        heartbeat,
        install,
        llm,
        mcp,
        memory,
        mesh,
        proactive,
        router_status,
        runtime,
        tasks,
    )
    from navig.messaging import is_provider_enabled

    raw_cfg = getattr(gateway.config_manager, "global_config", {}) or {}

    for mod in (
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
        router_status,
        mesh,
        runtime,
        daemon,
        install,
        audit,
        billing,
    ):
        mod.register(app, gateway)

    if is_provider_enabled("telegram", raw_cfg):
        from navig.gateway.routes import telegram_webhook

        telegram_webhook.register(app, gateway)
