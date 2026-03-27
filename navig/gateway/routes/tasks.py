"""Task queue routes: /tasks list/add/stats/get/cancel."""

from __future__ import annotations

try:
    from aiohttp import web  # noqa: F401
except ImportError as _exc:
    raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from _exc
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import (
    json_error_response,
    json_ok,
    require_bearer_auth,
)

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/tasks", _list(gateway))
    app.router.add_post("/tasks", _add(gateway))
    app.router.add_get("/tasks/stats", _stats(gateway))
    app.router.add_get("/tasks/{task_id}", _get(gateway))
    app.router.add_post("/tasks/{task_id}/cancel", _cancel(gateway))


def _chk(gw):
    if not gw.task_queue:
        return json_error_response(
            "Tasks module not available", status=503, code="module_unavailable"
        )
    return None


def _list(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        try:
            from navig.tasks import TaskStatus

            status_filter = r.query.get("status")
            status = TaskStatus(status_filter) if status_filter else None
            limit = int(r.query.get("limit", 50))
            tasks = await gw.task_queue.list_tasks(status=status, limit=limit)
            return json_ok({"tasks": [t.to_dict() for t in tasks]})
        except Exception as e:
            return json_error_response(
                "Failed to list tasks",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _add(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        try:
            from navig.tasks import Task

            data = await r.json()
            actor = r.headers.get("X-Actor", r.remote or "unknown")
            block = await gw.policy_check("task.add", actor, raw_input=str(data))
            if block is not None:
                return block
            task = Task(
                name=data["name"],
                handler=data["handler"],
                params=data.get("params", {}),
                priority=data.get("priority", 50),
                dependencies=data.get("dependencies", []),
                max_retries=data.get("max_retries", 0),
                timeout=data.get("timeout"),
            )
            task = await gw.task_queue.add(task)
            return json_ok(task.to_dict())
        except KeyError as e:
            return json_error_response(
                f"Missing required field: {e}", status=400, code="validation_error"
            )
        except Exception as e:
            return json_error_response(
                "Failed to add task",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _stats(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        stats = gw.task_queue.get_stats()
        if gw.task_worker:
            stats["worker"] = gw.task_worker.get_stats()
        return json_ok(stats)

    return h


def _get(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        try:
            task_id = r.match_info["task_id"]
            task = await gw.task_queue.get(task_id)
            if not task:
                return json_error_response("Task not found", status=404, code="not_found")
            return json_ok(task.to_dict())
        except Exception as e:
            return json_error_response(
                "Failed to get task",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h


def _cancel(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err:
            return err
        try:
            task_id = r.match_info["task_id"]
            task = await gw.task_queue.cancel(task_id)
            return json_ok({"task": task.to_dict()})
        except ValueError as e:
            return json_error_response(str(e), status=404, code="not_found")
        except Exception as e:
            return json_error_response(
                "Failed to cancel task",
                status=500,
                code="internal_error",
                details={"error": str(e)},
            )

    return h
