"""Cron routes: /cron/jobs CRUD + enable/disable/run."""
from __future__ import annotations

try:
    from aiohttp import web
except ImportError:
    pass
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth

logger = get_debug_logger()

def register(app, gateway):
    app.router.add_get("/cron/jobs", _list(gateway))
    app.router.add_post("/cron/jobs", _add(gateway))
    app.router.add_get("/cron/jobs/{job_id}", _get(gateway))
    app.router.add_delete("/cron/jobs/{job_id}", _delete(gateway))
    app.router.add_post("/cron/jobs/{job_id}/enable", _enable(gateway))
    app.router.add_post("/cron/jobs/{job_id}/disable", _disable(gateway))
    app.router.add_post("/cron/jobs/{job_id}/run", _run(gateway))

def _svc_or_503(gw):
    if not gw.cron_service:
        return None, json_error_response("Cron service not enabled", status=503, code="module_unavailable")
    return gw.cron_service, None

def _list(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        cs, err = _svc_or_503(gw)
        if err: return err
        try:
            jobs = cs.list_jobs()
            return json_ok({"jobs": [j.to_dict() for j in jobs]})
        except Exception as e:
            logger.exception("Failed to list cron jobs")
            return json_error_response("Failed to list cron jobs", status=500, code="internal_error", details={"error": str(e)})
    return h

def _add(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        cs, err = _svc_or_503(gw)
        if err: return err
        try:
            data = await r.json()
            job = cs.add_job(
                name=data["name"], schedule=data["schedule"],
                command=data["command"], enabled=data.get("enabled", True),
                timeout_seconds=data.get("timeout", 300))
            return json_ok(job.to_dict())
        except KeyError as e:
            return json_error_response(f"Missing required field: {e}", status=400, code="validation_error")
        except Exception as e:
            logger.exception("Failed to create cron job")
            return json_error_response("Failed to create cron job", status=500, code="internal_error", details={"error": str(e)})
    return h

def _get(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        cs, err = _svc_or_503(gw)
        if err: return err
        try:
            job = cs.get_job(r.match_info["job_id"])
            if not job:
                return json_error_response("Job not found", status=404, code="not_found")
            return json_ok(job.to_dict())
        except Exception as e:
            logger.exception("Failed to get cron job")
            return json_error_response("Failed to get cron job", status=500, code="internal_error", details={"error": str(e)})
    return h

def _delete(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        cs, err = _svc_or_503(gw)
        if err: return err
        try:
            ok = cs.remove_job(r.match_info["job_id"])
            if not ok:
                return json_error_response("Job not found", status=404, code="not_found")
            return json_ok({"deleted": True})
        except Exception as e:
            logger.exception("Failed to delete cron job")
            return json_error_response("Failed to delete cron job", status=500, code="internal_error", details={"error": str(e)})
    return h

def _enable(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        cs, err = _svc_or_503(gw)
        if err: return err
        try:
            ok = cs.enable_job(r.match_info["job_id"])
            if not ok:
                return json_error_response("Job not found", status=404, code="not_found")
            return json_ok({"enabled": True})
        except Exception as e:
            logger.exception("Failed to enable cron job")
            return json_error_response("Failed to enable cron job", status=500, code="internal_error", details={"error": str(e)})
    return h

def _disable(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        cs, err = _svc_or_503(gw)
        if err: return err
        try:
            ok = cs.disable_job(r.match_info["job_id"])
            if not ok:
                return json_error_response("Job not found", status=404, code="not_found")
            return json_ok({"disabled": True})
        except Exception as e:
            logger.exception("Failed to disable cron job")
            return json_error_response("Failed to disable cron job", status=500, code="internal_error", details={"error": str(e)})
    return h

def _run(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        cs, err = _svc_or_503(gw)
        if err: return err
        try:
            result = await cs.run_job_now(r.match_info["job_id"])
            if not result:
                return json_error_response("Job not found", status=404, code="not_found")
            return json_ok({
                "success": result.get("success", False),
                "output": result.get("output", ""),
                "error": result.get("error")})
        except Exception as e:
            logger.exception("Failed to run cron job")
            return json_error_response("Failed to run cron job", status=500, code="internal_error", details={"error": str(e)})
    return h
