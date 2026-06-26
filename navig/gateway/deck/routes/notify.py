"""Notification system REST handlers for the Deck.

    GET  /api/deck/notify/feed[?unread_only=]   → feed items + unread_count
    POST /api/deck/notify/feed/{id}/read        → mark one read
    POST /api/deck/notify/feed/read-all         → mark all read
    GET  /api/deck/notify/prefs                 → matrix + types + channels(+status) + settings
    POST /api/deck/notify/prefs                 → set a matrix cell OR a setting/target
    POST /api/deck/notify/test                  → send a test notification (verify a channel)

Same {ok, data} envelope as the other deck routes.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.notify import feed, prefs
from navig.notify.types import CHANNELS, NOTIFICATION_TYPES

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


# ── Feed ─────────────────────────────────────────────────────────────────────

async def handle_notify_feed_list(request: "web.Request") -> "web.Response":
    try:
        unread_only = request.query.get("unread_only") in ("1", "true", "yes")
        limit = int(request.query.get("limit", 50))
        return _ok({"items": feed.list_items(limit=limit, unread_only=unread_only),
                    "unread_count": feed.unread_count()})
    except Exception as exc:
        logger.exception("notify feed list failed")
        return _err(str(exc), 500)


async def handle_notify_feed_read(request: "web.Request") -> "web.Response":
    try:
        ok = feed.mark_read(request.match_info["id"])
        return _ok({"read": ok, "unread_count": feed.unread_count()})
    except Exception as exc:
        logger.exception("notify feed read failed")
        return _err(str(exc), 500)


async def handle_notify_feed_read_all(request: "web.Request") -> "web.Response":
    try:
        n = feed.mark_all_read()
        return _ok({"marked": n, "unread_count": feed.unread_count()})
    except Exception as exc:
        logger.exception("notify feed read-all failed")
        return _err(str(exc), 500)


# ── Preferences ──────────────────────────────────────────────────────────────

def _channel_status() -> dict[str, bool]:
    """Best-effort per-channel 'configured' flags for the settings UI."""
    status: dict[str, bool] = {"deck": True}
    try:
        from navig.gateway.notifications import get_notification_manager
        nm = get_notification_manager()
        getch = getattr(nm, "get_channel", None)
        status["telegram"] = bool(getch("telegram")) if getch else bool(getattr(nm, "telegram", None))
        status["matrix"] = bool(getch("matrix")) if getch else False
    except Exception:
        status.setdefault("telegram", False)
        status.setdefault("matrix", False)
    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        status["email"] = ConnectorAuthManager().is_connected("gmail")
    except Exception:
        status["email"] = False
    try:
        from navig.messaging.adapter_registry import get_adapter_registry
        reg = get_adapter_registry()
        status["sms"] = reg.is_available("sms") and bool(prefs.get_target("sms"))
        status["discord"] = reg.is_available("discord") and bool(prefs.get_target("discord"))
        status["whatsapp"] = (reg.is_available("whatsapp_cloud") or reg.is_available("whatsapp")) and bool(prefs.get_target("whatsapp"))
    except Exception:
        for k in ("sms", "discord", "whatsapp"):
            status.setdefault(k, False)
    return status


async def handle_notify_prefs_get(request: "web.Request") -> "web.Response":
    try:
        st = _channel_status()
        channels = [{**c, "configured": st.get(c["key"], False)} for c in CHANNELS]
        from navig.notify import signals

        return _ok({
            "matrix": prefs.get_matrix(),
            # Static taxonomy + one dynamic row per signal source (mutable per source).
            "types": [*NOTIFICATION_TYPES, *signals.dynamic_types()],
            "channels": channels,
            "settings": prefs.get_settings(),
        })
    except Exception as exc:
        logger.exception("notify prefs get failed")
        return _err(str(exc), 500)


async def handle_notify_prefs_post(request: "web.Request") -> "web.Response":
    body = await _body(request)
    try:
        # Matrix cell: {type, channel, enabled}
        if "type" in body and "channel" in body:
            prefs.set_cell(str(body["type"]), str(body["channel"]), bool(body.get("enabled")))
            return _ok({"updated": "cell"})
        # Setting / target: {key, value}
        if "key" in body:
            prefs.set_setting(str(body["key"]), body.get("value"))
            return _ok({"updated": "setting"})
        return _err("expected {type, channel, enabled} or {key, value}", 400)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("notify prefs post failed")
        return _err(str(exc), 500)


# ── Test ─────────────────────────────────────────────────────────────────────

async def handle_notify_test(request: "web.Request") -> "web.Response":
    body = await _body(request)
    channel = body.get("channel")
    try:
        from navig.notify.router import get_notification_router

        result = await get_notification_router().dispatch(
            "custom",
            "Test notification",
            f"This is a test{' to ' + channel if channel else ''} from navig.",
            priority="normal",
            only_channels=[channel] if channel else None,
        )
        return _ok(result)
    except Exception as exc:
        logger.exception("notify test failed")
        return _err(str(exc), 500)


async def handle_notify_briefing_now(request: "web.Request") -> "web.Response":
    """Compose + dispatch an AI briefing immediately (the 'Send now' button)."""
    try:
        from navig.notify.briefings import build_and_dispatch_briefing

        return _ok(await build_and_dispatch_briefing(force=True))
    except Exception as exc:
        logger.exception("notify briefing failed")
        return _err(str(exc), 500)


# ── Signals (inbound ingest sources) ─────────────────────────────────────────

def _ingest_url(name: str) -> str:
    """Public Lighthouse URL for *name* if configured, else the local gateway URL."""
    try:
        from navig.core import Config

        cfg = Config()
        url = (cfg.get("cloud.lighthouse_url") or "").strip()
        key = (cfg.get("deck.api_key") or "").strip()
        if url and key:
            from navig.cloud import api_key_hash

            return f"{url.rstrip('/')}/ingest/{api_key_hash(key)}/{name}"
    except Exception:
        logger.debug("ingest url resolve (public) failed", exc_info=True)
    try:
        from navig.gateway_client import gateway_base_url

        return f"{gateway_base_url().rstrip('/')}/api/ingest/{name}"
    except Exception:
        return f"/api/ingest/{name}"


async def handle_notify_signals_get(request: "web.Request") -> "web.Response":
    try:
        from navig.notify import signals
        from navig.notify.signal_presets import list_presets

        rows = signals.list_sources()
        for r in rows:
            r["ingest_url"] = _ingest_url(r["name"])
        return _ok({"sources": rows, "presets": list_presets()})
    except Exception as exc:
        logger.exception("notify signals get failed")
        return _err(str(exc), 500)


async def handle_notify_signals_post(request: "web.Request") -> "web.Response":
    body = await _body(request)
    action = str(body.get("action") or "").lower()
    name = str(body.get("name") or "").strip()
    try:
        from navig.notify import signals

        if action == "add":
            row = signals.add_source(
                name,
                preset=(body.get("preset") or None),
                notify_type=(body.get("type") or None),
                priority=(body.get("priority") or None),
                title_tmpl=body.get("title_tmpl") or None,
                body_tmpl=body.get("body_tmpl") or None,
            )
            row["ingest_url"] = _ingest_url(row["name"])
            return _ok({"created": row})  # full secret — shown once
        if action == "remove":
            return _ok({"removed": signals.remove_source(name)})
        if action == "rotate":
            return _ok({"name": name, "secret": signals.rotate_secret(name)})
        if action == "enable":
            return _ok({"updated": signals.set_enabled(name, bool(body.get("enabled", True)))})
        if action == "test":
            src = signals.get_source(name)
            if src is None:
                return _err("unknown_source", 404)
            nt, title, btext, prio, data = signals.render_event(src, signals.SAMPLE_PAYLOAD)
            data["_test"] = True
            from navig.notify import dispatch as notify_dispatch

            return _ok(await notify_dispatch(nt, title, btext, priority=prio, data=data))
        return _err("expected action add|remove|rotate|enable|test", 400)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("notify signals post failed")
        return _err(str(exc), 500)


# ── Monitors (local producers toggle) ────────────────────────────────────────

_MONITORS = [
    {"key": "self_errors",  "label": "NAVIG errors",      "desc": "Tell me when the daemon logs an error."},
    {"key": "connectivity", "label": "Brain reachability", "desc": "Tell me when the Lighthouse uplink drops or returns."},
    {"key": "resources",    "label": "Resource alerts",    "desc": "Disk / memory / CPU threshold alerts."},
    {"key": "webcam",       "label": "Webcam (privacy)",   "desc": "Alert when an app starts using your camera."},
]
_MONITOR_KEYS = {m["key"] for m in _MONITORS}


def _truthy(v: object) -> bool:
    return v in (True, "1", "true", "yes", "True")


def _monitor_availability(
    key: str, *, mode: str, is_win: bool, has_psutil: bool
) -> tuple[bool, str | None]:
    """Whether a monitor can actually do its job in this host/mode, and why not.

    A monitor stays *toggleable* even when unavailable (you can pre-arm intent),
    but the UI surfaces the requirement so silence is never mysterious.
    """
    if key == "webcam":
        return (is_win, None if is_win else "Windows only")
    if key == "connectivity":
        ok = mode == "lighthouse"
        return (ok, None if ok else "Needs Lighthouse for live detection")
    if key == "resources":
        return (has_psutil, None if has_psutil else "Needs psutil")
    return (True, None)


async def handle_notify_monitors_get(request: "web.Request") -> "web.Response":
    try:
        import sys

        from navig.core import Config

        cfg = Config()
        mode = str(cfg.get("cloud.mode") or "")
        is_win = sys.platform == "win32"
        try:
            import psutil  # noqa: F401

            has_psutil = True
        except Exception:
            has_psutil = False

        out = []
        for m in _MONITORS:
            available, requirement = _monitor_availability(
                m["key"], mode=mode, is_win=is_win, has_psutil=has_psutil
            )
            out.append({
                **m,
                "enabled": _truthy(cfg.get(f"monitors.{m['key']}.enabled")),
                "available": available,
                "requirement": requirement,
            })
        return _ok({"monitors": out})
    except Exception as exc:
        logger.exception("notify monitors get failed")
        return _err(str(exc), 500)


async def handle_notify_monitors_post(request: "web.Request") -> "web.Response":
    body = await _body(request)
    name = str(body.get("name") or "")
    if name not in _MONITOR_KEYS:
        return _err("unknown monitor", 400)

    # Fire a sample notification so the user can prove delivery end-to-end.
    if str(body.get("action") or "") == "test":
        try:
            from navig.notify.producers.samples import dispatch_monitor_test

            result = await dispatch_monitor_test(name)
            if result is None:
                return _err("no sample for monitor", 400)
            return _ok(result)
        except Exception as exc:
            logger.exception("notify monitor test failed")
            return _err(str(exc), 500)

    enabled = bool(body.get("enabled"))
    try:
        from navig.core import Config

        cfg = Config()
        cfg.set(f"monitors.{name}.enabled", enabled, scope="global")
        cfg.save(scope="global")
        # Apply live so the toggle takes effect without a restart.
        gw = request.app.get("gateway") if hasattr(request, "app") else None
        if gw is not None and hasattr(gw, "set_monitor_enabled"):
            try:
                gw.set_monitor_enabled(name, enabled)
            except Exception:
                logger.debug("live monitor toggle failed", exc_info=True)
        return _ok({"name": name, "enabled": enabled})
    except Exception as exc:
        logger.exception("notify monitors post failed")
        return _err(str(exc), 500)


async def handle_notify_sms_webhook(request: "web.Request") -> "web.Response":
    """Resolve the public URL and point Twilio's inbound webhook at it.
    GET previews the resolved URL; POST forces a (re)configure."""
    gw = request.app.get("gateway") if hasattr(request, "app") else None
    try:
        from navig.notify.sms_webhook_config import auto_configure, resolve_public_base

        if request.method == "GET":
            base = resolve_public_base(gw)
            from navig.notify import prefs
            return _ok({
                "base": base,
                "webhook_url": (f"{base}/sms/webhook" if base else None),
                "configured_url": prefs.get_raw("sms_webhook_url") or None,
            })
        return _ok(await auto_configure(gw, force=True))
    except Exception as exc:
        logger.exception("notify sms webhook config failed")
        return _err(str(exc), 500)
