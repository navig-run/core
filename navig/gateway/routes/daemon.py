"""Daemon lifecycle and formation endpoints."""

import asyncio
import subprocess
import sys

from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    json_error_response,
    json_ok,
    require_bearer_auth,
)

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/api/daemon/status", _daemon_status(gateway))
    app.router.add_post("/api/daemon/stop", _daemon_stop(gateway))
    app.router.add_post("/api/formation/start", _formation_start(gateway))


def _daemon_status(gw):
    async def h(request):
        # 1. Active formation
        active_formation = None
        try:
            from navig.formations.registry import get_registry

            formation = get_registry().get_active()
            if formation:
                active_formation = formation.name
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # 2. Active Nodes
        nodes = []
        try:
            if hasattr(gw, "_mesh_discovery") and gw._mesh_discovery:
                for p in gw._mesh_discovery.list_peers():
                    nodes.append(
                        {
                            "id": p.node_id,
                            "name": p.hostname,
                            "state": "active" if p.health == "online" else "idle",
                        }
                    )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # To ensure we have CORS enabled for Deck / Bridge if they hit it directly from browser
        resp = json_ok(
            {
                "daemonStatus": "UP",
                "activeFormation": active_formation,
                "nodes": nodes,
            }
        )
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    return h


def _daemon_stop(gw):
    async def h(request):
        auth = require_bearer_auth(request, gw)
        if auth is not None:
            return auth

        actor = request.headers.get("X-Actor", request.remote or "unknown")
        block = await gw.policy_check("system.stop", actor)
        if block is not None:
            return block

        logger.info("Daemon stop requested via /api/daemon/stop by actor=%s", actor)
        resp = json_ok({"status": "shutting_down", "message": "Daemon shutdown initiated"})
        resp.headers["Access-Control-Allow-Origin"] = "*"

        async def _d():
            await asyncio.sleep(0.5)
            await gw.stop()
            sys.exit(0)

        asyncio.create_task(_d())
        return resp

    return h


def _formation_start(gw):
    async def h(request):
        auth = require_bearer_auth(request, gw)
        if auth is not None:
            return auth

        actor = request.headers.get("X-Actor", request.remote or "unknown")
        block = await gw.policy_check("formation.start", actor)
        if block is not None:
            return block

        logger.info("Formation start requested via /api/formation/start by actor=%s", actor)
        try:
            data = await request.json()
        except Exception:
            data = {}

        formation_name = data.get("formation", "app_project")

        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "navig.cli",
                    "formation",
                    "start",
                    formation_name,
                ]
            )
            resp = json_ok({"status": "starting", "formation": formation_name})
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        except Exception as e:
            resp = json_error_response(
                "Failed to start formation", status=500, details={"error": str(e)}
            )
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

    return h
