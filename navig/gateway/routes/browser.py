"""Browser routes: /browser/status, navigate, click, fill, screenshot, stop."""
from __future__ import annotations
try:
    from aiohttp import web
except ImportError:
    pass
from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth

logger = get_debug_logger()


def register(app, gateway):
    app.router.add_get("/browser/status", _status(gateway))
    app.router.add_post("/browser/navigate", _navigate(gateway))
    app.router.add_post("/browser/click", _click(gateway))
    app.router.add_post("/browser/fill", _fill(gateway))
    app.router.add_post("/browser/screenshot", _screenshot(gateway))
    app.router.add_post("/browser/stop", _stop(gateway))


def _chk(gw):
    if not gw.browser_controller:
        return json_error_response("Browser module not available", status=503, code="module_unavailable")
    return None


def _status(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err: return err
        return json_ok(
            {
                "started": gw.browser_controller._browser is not None,
                "has_page": gw.browser_controller._page is not None,
            }
        )
    return h


def _navigate(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err: return err
        try:
            data = await r.json()
            await gw.browser_controller.navigate(data["url"])
            return json_ok({"url": data["url"]})
        except KeyError as e:
            return json_error_response(f"Missing required field: {e}", status=400, code="validation_error")
        except Exception as e:
            return json_error_response("Navigation failed", status=500, code="internal_error", details={"error": str(e)})
    return h


def _click(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err: return err
        try:
            data = await r.json()
            await gw.browser_controller.click(data["selector"])
            return json_ok({"clicked": True})
        except KeyError as e:
            return json_error_response(f"Missing required field: {e}", status=400, code="validation_error")
        except Exception as e:
            return json_error_response("Click failed", status=500, code="internal_error", details={"error": str(e)})
    return h


def _fill(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err: return err
        try:
            data = await r.json()
            await gw.browser_controller.fill(data["selector"], data["value"])
            return json_ok({"filled": True})
        except KeyError as e:
            return json_error_response(f"Missing required field: {e}", status=400, code="validation_error")
        except Exception as e:
            return json_error_response("Fill failed", status=500, code="internal_error", details={"error": str(e)})
    return h


def _screenshot(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err: return err
        try:
            data = await r.json() if r.can_read_body else {}
            path = await gw.browser_controller.screenshot(
                path=data.get("path"),
                full_page=data.get("full_page", False),
            )
            return json_ok({"path": path})
        except Exception as e:
            return json_error_response("Screenshot failed", status=500, code="internal_error", details={"error": str(e)})
    return h


def _stop(gw):
    async def h(r):
        auth = require_bearer_auth(r, gw)
        if auth is not None:
            return auth
        err = _chk(gw)
        if err: return err
        try:
            await gw.browser_controller.stop()
            return json_ok({"stopped": True})
        except Exception as e:
            return json_error_response("Browser stop failed", status=500, code="internal_error", details={"error": str(e)})
    return h
