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
    import aiohttp
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from _exc
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    envelope_error,
    envelope_ok,
    json_error_response,
    json_ok,
    require_bearer_auth,
)

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/", _root_handler(gateway))
    app.router.add_get("/health", _health(gateway))
    app.router.add_get("/health/services", _health_services(gateway))
    app.router.add_get("/status", _status(gateway))

    # Memory curation — built-in review page + localhost data API (no SPA needed).
    # Same handlers as the deck-auth /api/deck/memory/* mount; here they're gateway
    # -level so the /memory/review page can call them on 127.0.0.1 without a token.
    from navig.gateway.deck.routes.memory import (
        handle_memory_approve,
        handle_memory_export,
        handle_memory_import,
        handle_memory_pending,
        handle_memory_reject,
        handle_memory_review_page,
    )

    app.router.add_get("/memory/review", handle_memory_review_page)
    app.router.add_get("/api/memory/facts/pending", handle_memory_pending)
    app.router.add_post("/api/memory/facts/{fact_id}/approve", handle_memory_approve)
    app.router.add_post("/api/memory/facts/{fact_id}/reject", handle_memory_reject)
    app.router.add_get("/api/memory/facts/export", handle_memory_export)
    app.router.add_post("/api/memory/facts/import", handle_memory_import)
    app.router.add_post("/shutdown", _shutdown(gateway))
    app.router.add_post("/message", _message(gateway))
    app.router.add_post("/event", _event(gateway))
    app.router.add_get("/sessions", _sessions(gateway))
    app.router.add_get("/ws", _websocket(gateway))


def _root_handler(gw):
    """Default `/` handler.

    Serves the Deck SPA's ``index.html`` directly at the root path when its
    static bundle is available. The Deck build uses no ``basePath``, so root is
    its natural mount (its JS/CSS are already served from ``/_next/``). When the
    Deck isn't built/enabled, falls back to a tiny info page so the operator
    knows the daemon is alive and what's actually mounted.
    """
    async def h(_r):
        # Serve the Deck SPA at "/" when its static bundle exists.
        try:
            from navig.gateway.deck.routes.static_assets import _find_deck_static_dir

            static_dir = _find_deck_static_dir()
            if static_dir is not None:
                return web.FileResponse(static_dir / "index.html")
        except Exception:
            # Never let a serving hiccup crash "/": fall through to the info page.
            pass
        # Deck not mounted (no bot token, etc.) — show a one-pager so
        # the operator knows the daemon is alive and what URLs work.
        html = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>NAVIG Gateway</title>"
            "<style>body{font:14px/1.5 system-ui,sans-serif;max-width:560px;"
            "margin:48px auto;padding:0 24px;color:#222}"
            "code{background:#f3f3f3;padding:2px 6px;border-radius:3px}"
            "h1{font-size:18px;margin:0 0 16px}a{color:#0066cc}</style>"
            "<h1>NAVIG Gateway — online</h1>"
            "<p>The daemon is running. The Deck UI is not currently mounted "
            "(no Telegram bot token configured, or <code>deck.enabled</code> "
            "is false).</p>"
            "<p>Useful endpoints:</p>"
            "<ul>"
            "<li><a href='/health'>/health</a> — liveness</li>"
            "<li><a href='/status'>/status</a> — runtime status</li>"
            "<li><a href='/heartbeat/history?limit=5'>/heartbeat/history</a> — recent checks</li>"
            "</ul>"
            "<p>Run <code>navig init</code> if you haven't configured the Deck yet.</p>"
        )
        return web.Response(text=html, content_type="text/html")
    return h


def _health(gw):
    async def h(r):
        return json_ok({"status": "ok", "timestamp": datetime.now().isoformat()})

    return h


def _health_services(gw):
    """Per-subsystem health snapshot (cloud manager, scheduler, heartbeat, …).

    Makes silent subsystem failures observable — e.g. a dead cloudflared tunnel
    shows ``cloud_manager: down`` while the gateway itself stays up.
    """
    async def h(r):
        registry = getattr(gw, "service_registry", None)
        snap = registry.snapshot() if registry is not None else {"status": "unknown", "services": {}}
        # Verifier observability — enabled state + recent adversarial verdicts.
        try:
            from navig.agent.verifier import get_recent_verdicts, get_verifier

            recent = get_recent_verdicts(limit=10)
            snap["verifier"] = {
                "enabled": get_verifier().enabled,
                "recent_count": len(recent),
                "recent_blocks": sum(1 for v in recent if not v.get("safe", True)),
                "recent": recent,
            }
        except Exception:  # noqa: BLE001
            pass
        snap["timestamp"] = datetime.now().isoformat()
        return json_ok(snap)

    return h


def _shutdown(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth

        actor = r.headers.get("X-Actor", r.remote or "unknown")
        block = await gw.policy_check("system.shutdown", actor)
        if block is not None:
            return block

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
                "created_at": (session.created_at.isoformat() if session.created_at else None),
                "updated_at": (session.updated_at.isoformat() if session.updated_at else None),
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
        connections = gw.__dict__.setdefault("ws_connections", set())
        subscriptions[cid] = set()
        connections.add(ws)

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
            connections.discard(ws)
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
            await ws.send_json(
                envelope_error("Missing required field: topic", code="validation_error")
            )
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
            await ws.send_json(
                envelope_error("Missing required field: message", code="validation_error")
            )
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
