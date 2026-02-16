"""Heartbeat routes: /heartbeat/trigger, /heartbeat/history, /heartbeat/status."""
from __future__ import annotations
try:
    from aiohttp import web
except ImportError:
    pass
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth

logger = get_debug_logger()

def register(app, gateway):
    app.router.add_post("/heartbeat/trigger", _trigger(gateway))
    app.router.add_get("/heartbeat/history", _history(gateway))
    app.router.add_get("/heartbeat/status", _status(gateway))

def _trigger(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        if not gw.heartbeat_runner:
            return json_error_response("Heartbeat not enabled", status=503, code="module_unavailable")
        try:
            result = await gw.heartbeat_runner.trigger_now()
            return json_ok({
                "success": result.success,
                "suppressed": result.suppressed,
                "response": result.response,
                "issues": result.issues_found,
                "timestamp": result.timestamp.isoformat()
            })
        except Exception as e:
            logger.exception("Heartbeat trigger failed")
            return json_error_response("Heartbeat trigger failed", status=500, code="internal_error", details={"error": str(e)})
    return h

def _history(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        if not gw.heartbeat_runner:
            return json_error_response("Heartbeat not enabled", status=503, code="module_unavailable")
        try:
            limit = int(r.query.get("limit", 10))
            history = gw.heartbeat_runner.get_history(limit=limit)
            return json_ok({"history": history})
        except Exception as e:
            logger.exception("Failed to get heartbeat history")
            return json_error_response("Failed to get heartbeat history", status=500, code="internal_error", details={"error": str(e)})
    return h

def _status(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        if not gw.heartbeat_runner:
            return json_error_response("Heartbeat not enabled", status=503, code="module_unavailable")
        try:
            status = gw.heartbeat_runner.get_status()
            return json_ok(status)
        except Exception as e:
            logger.exception("Failed to get heartbeat status")
            return json_error_response("Failed to get heartbeat status", status=500, code="internal_error", details={"error": str(e)})
    return h
