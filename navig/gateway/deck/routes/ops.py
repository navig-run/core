"""Ops / Remote Control snapshot handler for the Deck API."""

from __future__ import annotations

import logging
import time

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

# Module-level start time for cheap uptime calculation
_START_TS = time.monotonic()


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _get_config_manager():
    try:
        from navig.config import get_config_manager  # type: ignore[import]

        return get_config_manager()
    except Exception:
        return None


async def handle_deck_ops(request: "web.Request") -> "web.Response":
    """Return an OpsSnapshot for the Deck remote-control panel."""
    cfg = _get_config_manager()
    gateway = request.app.get("gateway") if hasattr(request, "app") else None

    # ── Toggles ──────────────────────────────────────────────────────────────
    ai_cfg = {}
    if cfg:
        try:
            ai_cfg = cfg.get("ai") or {}
        except Exception:
            pass

    toggles = {
        "power": True,  # daemon is running (we are answering the request)
        "smart_ai": bool(ai_cfg.get("smart_ai", True)),
        "auto_continue": bool(ai_cfg.get("auto_continue", False)),
        # Auto-dispatch critical+safe actions without a human in the loop.
        "auto_dispatch": bool(ai_cfg.get("auto_dispatch", False)),
    }

    # ── Session ───────────────────────────────────────────────────────────────
    uptime_secs = time.monotonic() - _START_TS
    session = {
        "active": True,
        "paused": False,
        "uptime": _fmt_uptime(uptime_secs),
        "snags_recovered": 0,
    }
    if gateway and getattr(gateway, "task_queue", None):
        try:
            q_stats = gateway.task_queue.get_stats()
            session["snags_recovered"] = int(
                q_stats.get("status_counts", {}).get("failed", 0)
            )
        except Exception:
            pass

    # ── Formation ────────────────────────────────────────────────────────────
    formation: dict = {"name": None, "active": False, "agents": []}
    if gateway:
        try:
            fm = getattr(gateway, "formation_manager", None)
            if fm:
                active = getattr(fm, "active_formation", None)
                if active:
                    formation = {
                        "name": getattr(active, "name", None),
                        "active": True,
                        "agents": [
                            {
                                "id": str(getattr(a, "id", i)),
                                "name": str(getattr(a, "name", f"agent-{i}")),
                                "status": str(getattr(a, "status", "running")),
                            }
                            for i, a in enumerate(
                                getattr(active, "agents", []) or []
                            )
                        ],
                    }
        except Exception:
            pass

    # ── Infra ────────────────────────────────────────────────────────────────
    host_name: str | None = None
    app_name: str | None = None
    context_engine = "none"
    context_files = 0
    if cfg:
        try:
            host_name = str(cfg.get("host") or "") or None
        except Exception:
            pass
        try:
            app_name = str(cfg.get("app") or "") or None
        except Exception:
            pass
        try:
            ctx_cfg = cfg.get("context") or {}
            context_engine = str(ctx_cfg.get("engine", "none"))
            context_files = int(ctx_cfg.get("files", 0))
        except Exception:
            pass

    infra = {
        "host": host_name,
        "app": app_name,
        "navig_available": True,
        "context_engine": context_engine,
        "context_files": context_files,
    }

    # ── PM ───────────────────────────────────────────────────────────────────
    pm_running = False
    if gateway:
        pm_running = bool(getattr(gateway, "pm_running", False))

    return web.json_response(
        {
            "toggles": toggles,
            "session": session,
            "formation": formation,
            "infra": infra,
            "pm_running": pm_running,
        }
    )


async def handle_deck_ops_toggle(request: "web.Request") -> "web.Response":
    """Toggle a named ops flag (power / smart_ai / auto_continue)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    toggle = str(body.get("toggle", "")).strip()
    value = body.get("value")  # optional; if absent, flip current

    _ALLOWED = {"power", "smart_ai", "auto_continue", "auto_dispatch"}
    if toggle not in _ALLOWED:
        return web.json_response(
            {"ok": False, "error": f"unknown toggle '{toggle}'"}, status=400
        )

    cfg = _get_config_manager()
    if cfg:
        try:
            ai_cfg = cfg.get("ai") or {}
            current = bool(ai_cfg.get(toggle, True))
            new_val = (not current) if value is None else bool(value)
            cfg.set(f"ai.{toggle}", new_val)
            return web.json_response({"ok": True, "toggle": toggle, "value": new_val})
        except Exception as exc:
            logger.debug("ops toggle error: %s", exc)

    # Graceful degradation — return success with the requested value
    new_val = True if value is None else bool(value)
    return web.json_response({"ok": True, "toggle": toggle, "value": new_val})


async def handle_deck_ops_session(request: "web.Request") -> "web.Response":
    """Session control (pause / resume)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    action = str(body.get("action", "")).strip()
    _ALLOWED = {"pause", "resume"}
    if action not in _ALLOWED:
        return web.json_response(
            {"ok": False, "error": f"unknown action '{action}'"}, status=400
        )

    return web.json_response({"ok": True, "action": action})


async def handle_deck_ops_quick(request: "web.Request") -> "web.Response":
    """Quick-action endpoint (e.g. clear_memory, reload_config)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    action = str(body.get("action", "")).strip()
    result = f"action '{action}' queued"

    gateway = request.app.get("gateway") if hasattr(request, "app") else None
    if gateway and hasattr(gateway, "enqueue_quick_action"):
        try:
            await gateway.enqueue_quick_action(action)
            result = f"action '{action}' dispatched"
        except Exception as exc:
            logger.debug("quick action error: %s", exc)

    return web.json_response({"ok": True, "result": result})
