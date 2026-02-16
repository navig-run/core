"""Approval routes: /approval/pending, /approval/request, /approval/{id}/respond."""
from __future__ import annotations
import asyncio
try:
    from aiohttp import web
except ImportError:
    pass
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/approval/pending", _pending(gateway))
    app.router.add_post("/approval/request", _request(gateway))
    app.router.add_post("/approval/{request_id}/respond", _respond(gateway))


def _pending(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        if not gw.approval_manager:
            return json_error_response("Approval module not available", status=503, code="module_unavailable")
        pending = gw.approval_manager.list_pending()
        return json_ok(
            {
                "pending": [
                    {
                        "id": req.id,
                        "command": req.command,
                        "level": req.level.value,
                        "description": req.description,
                        "user_id": req.user_id,
                        "created_at": req.created_at.isoformat(),
                    }
                    for req in pending
                ]
            }
        )
    return h


def _request(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        if not gw.approval_manager:
            return json_error_response("Approval module not available", status=503, code="module_unavailable")
        try:
            data = await r.json()
            approved = await gw.approval_manager.request_approval(
                command=data["command"],
                description=data.get("description", ""),
                user_id=data.get("user_id", "anonymous"),
                channel=data.get("channel", "api"),
                session_key=data.get("session_key", "api:default"),
            )
            return json_ok({"approved": approved})
        except KeyError as e:
            return json_error_response(
                f"Missing required field: {e}",
                status=400,
                code="validation_error",
            )
        except asyncio.TimeoutError:
            return json_error_response("Approval timed out", status=408, code="timeout")
        except Exception as e:
            return json_error_response(
                "Approval request failed",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )
    return h


def _respond(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        if not gw.approval_manager:
            return json_error_response("Approval module not available", status=503, code="module_unavailable")
        try:
            request_id = r.match_info["request_id"]
            data = await r.json()
            approved = data.get("approved", False)
            success = await gw.approval_manager.respond(
                request_id=request_id, approved=approved,
            )
            if success:
                return json_ok({"success": True})
            else:
                return json_error_response("Request not found", status=404, code="not_found")
        except Exception as e:
            return json_error_response(
                "Approval response failed",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )
    return h
