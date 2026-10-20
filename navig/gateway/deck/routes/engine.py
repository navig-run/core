"""Engine control for the Deck API — what NAVIG OS and the Mini App drive the engine with.

Routes:
    GET  /api/deck/engine          -> daemon + bot + gateway + cloud-uplink status
    POST /api/deck/engine/restart  -> restart the daemon (the one safe path)
    GET  /api/deck/engine/doctor   -> the health report (``navig doctor --json``)
    POST /api/deck/engine/heal     -> ``navig doctor --heal --json``

These sit under ``/api/deck`` on purpose. ``/api/daemon/*`` authenticates with the
gateway's own bearer token, which neither the Mini App (Telegram initData / deck key,
through Lighthouse) nor NAVIG OS (loopback) carries — which is why no surface ever
used it. Deck auth is what both can satisfy, and the restart still goes through the
gateway's policy gate. Every action shares its seam with Telegram's ``/restart`` and
``/doctor`` (``request_daemon_restart``, ``navig.daemon.engine_ops``), so the three
surfaces can never disagree.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.gateway.deck.routes._enforce import deck_actor

logger = logging.getLogger(__name__)


def _gateway(request: "web.Request") -> Any:
    return request.app.get("gateway") or request.app.get("navig_gateway")


def _cloud_summary(gw: Any) -> dict[str, Any]:
    cm = getattr(gw, "cloud_manager", None) if gw is not None else None
    if cm is None:
        return {"status": "off", "url": None}
    return {
        "status": str(getattr(cm, "status", "unknown") or "unknown"),
        "url": getattr(cm, "current_url", None),
    }


def _daemon_up() -> bool | None:
    try:
        from navig.daemon.supervisor import NavigDaemon

        return NavigDaemon.is_running()
    except Exception:  # noqa: BLE001
        return None


async def handle_deck_engine_status(request: "web.Request") -> "web.Response":
    from navig.gateway.routes.daemon import _engine_facts

    facts = await asyncio.to_thread(_engine_facts)
    daemon_up = await asyncio.to_thread(_daemon_up)
    return web.json_response(
        {
            "ok": True,
            "data": {
                **facts,
                "daemonRunning": daemon_up,
                # The gateway answering this request IS the proof it is up.
                "gatewayAnswering": True,
                "cloud": _cloud_summary(_gateway(request)),
            },
        }
    )


async def handle_deck_engine_restart(request: "web.Request") -> "web.Response":
    from navig.gateway.routes.daemon import request_daemon_restart

    gw = _gateway(request)
    actor = deck_actor(request)  # server-derived; X-Actor is client-settable and ignored
    if gw is not None and hasattr(gw, "policy_check"):
        block = await gw.policy_check("system.restart", actor)
        if block is not None:
            return block
    logger.info("Engine restart requested via /api/deck/engine/restart by actor=%s", actor)
    out = await request_daemon_restart("deck /api/deck/engine/restart", actor=actor)
    status = int(out.pop("http_status", 200))
    if out.get("ok"):
        return web.json_response({"ok": True, "data": out}, status=status)
    return web.json_response(
        {"ok": False, "error": out.get("message") or "restart failed", "data": out}, status=status
    )


async def handle_deck_engine_doctor(request: "web.Request") -> "web.Response":
    from navig.daemon.engine_ops import collect_doctor

    try:
        report = await collect_doctor()
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "error": "the health check timed out"}, status=504)
    except Exception as exc:  # noqa: BLE001 — report the failure, never a green verdict
        logger.warning("engine doctor failed: %s", exc)
        return web.json_response({"ok": False, "error": f"health check failed: {exc}"}, status=500)
    return web.json_response({"ok": True, "data": report})


async def handle_deck_engine_heal(request: "web.Request") -> "web.Response":
    from navig.daemon.engine_ops import run_heal

    gw = _gateway(request)
    actor = deck_actor(request)  # server-derived; X-Actor is client-settable and ignored
    if gw is not None and hasattr(gw, "policy_check"):
        block = await gw.policy_check("system.heal", actor)
        if block is not None:
            return block
    try:
        result = await run_heal()
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "error": "heal timed out"}, status=504)
    except (ValueError, OSError) as exc:
        return web.json_response({"ok": False, "error": f"heal produced no result: {exc}"}, status=500)
    return web.json_response({"ok": True, "data": result})


def register(app: "web.Application") -> None:
    app.router.add_get("/api/deck/engine", handle_deck_engine_status)
    app.router.add_post("/api/deck/engine/restart", handle_deck_engine_restart)
    app.router.add_get("/api/deck/engine/doctor", handle_deck_engine_doctor)
    app.router.add_post("/api/deck/engine/heal", handle_deck_engine_heal)
