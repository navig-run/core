"""Proactive agent routes: /proactive/status, start, stop, check, /engagement/status, /engagement/tick."""

from __future__ import annotations

import asyncio

try:
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError(
        "aiohttp is required for gateway routes (pip install aiohttp)"
    ) from _exc
from navig.agent.proactive.engine import get_proactive_engine
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    json_error_response,
    json_ok,
    require_bearer_auth,
)

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/proactive/status", _proactive_status(gateway))
    app.router.add_post("/proactive/start", _proactive_start(gateway))
    app.router.add_post("/proactive/stop", _proactive_stop(gateway))
    app.router.add_post("/proactive/check", _proactive_check(gateway))
    app.router.add_get("/engagement/status", _engagement_status(gateway))
    app.router.add_post("/engagement/tick", _engagement_tick(gateway))


def _proactive_status(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        engine = get_proactive_engine()
        return json_ok(
            {
                "started": engine.running,
                "last_check": (
                    engine.last_check.isoformat() if engine.last_check else None
                ),
                "last_check_status": engine.last_check_status,
                "last_error": engine.last_error,
                "providers": engine.provider_status,
            }
        )

    return h


def _proactive_start(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        engine = get_proactive_engine()
        if not engine.running:
            asyncio.create_task(engine.start())
            return json_ok({"status": "started"})
        return json_ok({"status": "already_running"})

    return h


def _proactive_stop(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        engine = get_proactive_engine()
        if engine.running:
            await engine.stop()
            return json_ok({"status": "stopped"})
        return json_ok({"status": "not_running"})

    return h


def _proactive_check(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        engine = get_proactive_engine()
        if engine.is_checking:
            return json_error_response("Proactive engine busy", status=409, code="busy")
        asyncio.create_task(engine.run_checks(None))
        return json_ok({"status": "triggered"})

    return h


def _engagement_status(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            engine = get_proactive_engine()
            coordinator = engine._get_engagement_coordinator()
            state = coordinator.state
            return json_ok(
                {
                    "enabled": coordinator.config.enabled,
                    "operator_state": state.get_operator_state().value,
                    "time_of_day": state.get_time_of_day().value,
                    "within_active_hours": state.is_within_active_hours(),
                    "stats": {
                        "total_messages": state.stats.total_messages,
                        "total_commands": state.stats.total_commands,
                        "features_used": len(state.stats.features_used),
                        "last_greeting": state.stats.last_greeting,
                        "last_checkin": state.stats.last_checkin,
                        "last_capability_promo": state.stats.last_capability_promo,
                        "last_feedback_ask": state.stats.last_feedback_ask,
                    },
                    "daily_sends": len(coordinator._daily_sends),
                    "max_daily": coordinator.config.max_proactive_per_day,
                }
            )
        except Exception as e:
            return json_error_response(
                "Failed to get engagement status",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _engagement_tick(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        try:
            engine = get_proactive_engine()
            coordinator = engine._get_engagement_coordinator()
            result = coordinator.engagement_tick()
            if result:
                if "telegram" in gw.channels:
                    await gw.deliver_message(
                        channel="telegram",
                        to=None,
                        content=result.message,
                    )
                return json_ok(
                    {
                        "status": "sent",
                        "action": result.action.value,
                        "message": result.message,
                        "priority": result.priority,
                    }
                )
            return json_ok({"status": "no_action"})
        except Exception as e:
            return json_error_response(
                "Failed to run engagement tick",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h
