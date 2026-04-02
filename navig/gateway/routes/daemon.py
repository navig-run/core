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
    app.router.add_post("/api/daemon/start", _daemon_start(gateway))
    app.router.add_post("/api/daemon/stop", _daemon_stop(gateway))
    app.router.add_post("/api/daemon/restart", _daemon_restart(gateway))
    app.router.add_post("/api/formation/start", _formation_start(gateway))


async def _run_service_action(action: str, *, timeout_seconds: float = 30.0) -> tuple[bool, str, str]:
    """Run `navig service <action>` and normalize outcome for API responses."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "navig.cli",
        "service",
        action,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        return (False, f"Timed out while executing `navig service {action}`", "timeout")

    output = (stdout.decode("utf-8", errors="ignore") + "\n" + stderr.decode("utf-8", errors="ignore")).strip()
    lowered = output.lower()

    if action == "start":
        if "already running" in lowered:
            return (True, "Daemon already running", "noop_already_running")
        if process.returncode == 0:
            return (True, "Daemon started", "started")
        return (False, output or "Failed to start daemon", "start_failed")

    if action == "stop":
        if "is not running" in lowered or "already stopped" in lowered:
            return (True, "Daemon already stopped", "noop_already_stopped")
        if process.returncode == 0:
            return (True, "Daemon stopped", "stopped")
        return (False, output or "Failed to stop daemon", "stop_failed")

    if action == "restart":
        if process.returncode == 0:
            return (True, "Daemon restarted", "restarted")
        return (False, output or "Failed to restart daemon", "restart_failed")

    return (False, f"Unsupported action: {action}", "unsupported_action")


def _daemon_start(gw):
    async def h(request):
        auth = require_bearer_auth(request, gw)
        if auth is not None:
            return auth

        actor = request.headers.get("X-Actor", request.remote or "unknown")
        block = await gw.policy_check("system.start", actor)
        if block is not None:
            return block

        logger.info("Daemon start requested via /api/daemon/start by actor=%s", actor)
        ok, message, result_code = await _run_service_action("start")
        if ok:
            resp = json_ok({"ok": True, "status": "started", "message": message, "result_code": result_code})
        else:
            resp = json_error_response(
                "Failed to start daemon",
                status=500,
                code="daemon_start_failed",
                details={"message": message, "result_code": result_code},
            )
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    return h


def _daemon_status(gw):
    async def h(request):
        daemon_status_value = "UNKNOWN"
        try:
            from navig.daemon.supervisor import NavigDaemon

            daemon_status_value = "UP" if NavigDaemon.is_running() else "DOWN"
        except Exception:  # noqa: BLE001
            daemon_status_value = "UNKNOWN"

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
                "daemonStatus": daemon_status_value,
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
        resp = json_ok(
            {
                "ok": True,
                "status": "stopping",
                "message": "Daemon stop initiated",
                "result_code": "stopping",
            }
        )
        resp.headers["Access-Control-Allow-Origin"] = "*"

        async def _d():
            await asyncio.sleep(0.25)
            subprocess.Popen(
                [sys.executable, "-m", "navig.cli", "service", "stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        asyncio.create_task(_d())
        return resp

    return h


def _daemon_restart(gw):
    async def h(request):
        auth = require_bearer_auth(request, gw)
        if auth is not None:
            return auth

        actor = request.headers.get("X-Actor", request.remote or "unknown")
        block = await gw.policy_check("system.restart", actor)
        if block is not None:
            return block

        logger.info("Daemon restart requested via /api/daemon/restart by actor=%s", actor)
        ok, message, result_code = await _run_service_action("restart")
        if ok:
            resp = json_ok({"ok": True, "status": "restarted", "message": message, "result_code": result_code})
        else:
            resp = json_error_response(
                "Failed to restart daemon",
                status=500,
                code="daemon_restart_failed",
                details={"message": message, "result_code": result_code},
            )
        resp.headers["Access-Control-Allow-Origin"] = "*"
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
