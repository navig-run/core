"""Core gateway routes: /health, /status, /shutdown, /message, /event, /sessions, /ws."""
from __future__ import annotations
import asyncio
import json
import sys
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web
    from navig.gateway.server import NavigGateway  # noqa: F401
try:
    from aiohttp import web
    import aiohttp
except ImportError:
    pass
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    envelope_ok,
    envelope_error,
    json_error_response,
    json_ok,
    require_bearer_auth,
)

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/health", _health(gateway))
    app.router.add_get("/status", _status(gateway))
    app.router.add_post("/shutdown", _shutdown(gateway))
    app.router.add_post("/message", _message(gateway))
    app.router.add_post("/event", _event(gateway))
    app.router.add_get("/sessions", _sessions(gateway))
    app.router.add_get("/ws", _websocket(gateway))

def _health(gw):
    async def h(r):
        return json_ok({"status": "ok", "timestamp": datetime.now().isoformat()})

    return h


def _shutdown(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        logger.info("Shutdown requested via API")

        resp = json_ok({"status": "shutting_down", "message": "Gateway shutdown initiated"})

        async def _d():
            await asyncio.sleep(0.5)
            await gw.stop()
            sys.exit(0)

        asyncio.create_task(_d())
        return resp

    return h


def _status(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        try:
            uptime = (datetime.now() - gw.start_time).total_seconds() if gw.start_time else None
            hb = None
            if gw.heartbeat_runner:
                hb = {
                    "running": gw.heartbeat_runner.running,
                    "last_run": (
                        gw.heartbeat_runner._last_heartbeat.isoformat()
                        if gw.heartbeat_runner._last_heartbeat
                        else None
                    ),
                    "next_run": (
                        gw.heartbeat_runner._next_heartbeat.isoformat()
                        if gw.heartbeat_runner._next_heartbeat
                        else None
                    ),
                }
            cr = None
            if gw.cron_service:
                cr = {
                    "jobs": len(gw.cron_service.jobs),
                    "enabled_jobs": sum(1 for j in gw.cron_service.jobs.values() if j.enabled),
                }
            return json_ok(
                {
                    "status": "running" if gw.running else "stopped",
                    "uptime_seconds": uptime,
                    "config": {
                        "port": gw.config.port,
                        "host": gw.config.host,
                        "heartbeat_enabled": gw.config.heartbeat_enabled,
                        "heartbeat_interval": gw.config.heartbeat_interval,
                    },
                    "sessions": {"active": len(gw.sessions.sessions)},
                    "heartbeat": hb,
                    "cron": cr,
                }
            )
        except Exception as e:
            logger.exception("Status endpoint error")
            return json_error_response(
                "Failed to fetch status",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _message(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        try:
            data = await r.json()
        except json.JSONDecodeError:
            return json_error_response("Invalid JSON", status=400, code="invalid_json")

        for field in ("channel", "user_id", "message"):
            if field not in data:
                return json_error_response(
                    f"Missing required field: {field}",
                    status=400,
                    code="validation_error",
                )

        try:
            resp = await gw.router.route_message(
                channel=data["channel"],
                user_id=data["user_id"],
                message=data["message"],
                metadata=data.get("metadata", {}),
            )
            return json_ok({"response": resp})
        except Exception as e:
            logger.error("Error handling message: %s", e)
            return json_error_response(
                "Failed to route message",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _event(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        try:
            data = await r.json()
        except json.JSONDecodeError:
            return json_error_response("Invalid JSON", status=400, code="invalid_json")

        text = data.get("text")
        if not text:
            return json_error_response(
                "Missing required field: text",
                status=400,
                code="validation_error",
            )

        await gw.system_events.enqueue(text=text, agent_id=data.get("agent_id", "default"))
        if data.get("wake_now") and gw.heartbeat_runner:
            await gw.heartbeat_runner.request_run_now()

        return json_ok({"message": "Event queued"})

    return h


def _sessions(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        session_list = [
            {
                "key": key,
                "message_count": len(session.messages),
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            }
            for key, session in gw.sessions.sessions.items()
        ]
        return json_ok({"sessions": session_list, "total": len(session_list)})

    return h


def _websocket(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        ws = web.WebSocketResponse()
        await ws.prepare(r)

        cid = id(ws)
        logger.info("WS connected: %s", cid)
        subscriptions = gw.__dict__.setdefault("_ws_subscriptions", {})
        subscriptions[cid] = set()

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await _ws_dispatch(ws, data, gw)
                    except json.JSONDecodeError:
                        await ws.send_json(envelope_error("Invalid JSON", code="invalid_json"))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("WS error: %s", ws.exception())
        finally:
            subscriptions.pop(cid, None)
            logger.info("WS disconnected: %s", cid)
        return ws

    return h


async def _ws_dispatch(ws, data, gw):
    a = data.get("action")
    if a == "ping":
        await ws.send_json({"action": "pong", **envelope_ok({"action": "pong"})})
        return

    if a == "subscribe":
        topic = data.get("topic")
        if not topic:
            await ws.send_json(envelope_error("Missing required field: topic", code="validation_error"))
            return

        cid = id(ws)
        subscriptions = gw.__dict__.setdefault("_ws_subscriptions", {})
        subscriptions.setdefault(cid, set()).add(str(topic))
        await ws.send_json(
            {
                "action": "subscribed",
                "topic": topic,
                "subscriptions": sorted(subscriptions[cid]),
                **envelope_ok({"action": "subscribed", "topic": topic}),
            }
        )
        return

    if a == "message":
        message = data.get("message")
        if message is None:
            await ws.send_json(envelope_error("Missing required field: message", code="validation_error"))
            return

        resp = await gw.router.route_message(
            channel=data.get("channel", "ws"),
            user_id=data.get("user_id", "anonymous"),
            message=message,
            metadata=data.get("metadata", {}),
        )
        await ws.send_json({"action": "response", **envelope_ok({"response": resp})})
        return

    await ws.send_json(
        envelope_error(
            "Unsupported websocket action",
            code="unsupported_action",
            details={"action": a},
        )
    )
