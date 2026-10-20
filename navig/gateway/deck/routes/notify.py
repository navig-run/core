"""Notification system REST handlers for the Deck.

    GET  /api/deck/notify/feed[?unread_only=]   → feed items + unread_count
    POST /api/deck/notify/feed/{id}/read        → mark one read
    POST /api/deck/notify/feed/read-all         → mark all read
    GET  /api/deck/notify/prefs                 → matrix + types + channels(+status) + settings
    POST /api/deck/notify/prefs                 → set a matrix cell OR a setting/target
    POST /api/deck/notify/test                  → send a test notification (verify a channel)
    POST /api/deck/notify/telegram-route        → route a type to its own Telegram chat /
                                                  set its sound, or send a test there
    POST /api/deck/notify/pushover              → validate + save Pushover keys, or test

Same {ok, data} envelope as the other deck routes.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.core.coerce import coerce_bool
from navig.notify import feed, health, prefs, telegram_routes
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
    # Telegram delivers through the Bot API directly (navig.notify.telegram_routes),
    # not through a NotificationManager channel — that object is never configured
    # in either daemon process, so asking it reported "not configured" on every
    # working install. Ready = a bot token AND a chat to send to.
    if not status.get("telegram"):
        try:
            from navig.messaging.notify_operator import resolve_operator_chat_id
            from navig.messaging.secrets import resolve_telegram_bot_token

            status["telegram"] = bool(resolve_telegram_bot_token()) and bool(
                prefs.get_target("telegram") or resolve_operator_chat_id()
            )
        except Exception:
            status["telegram"] = False
    try:
        from navig.notify import pushover

        status["pushover"] = pushover.is_configured()
    except Exception:
        status["pushover"] = False
    try:
        from navig.connectors.auth_manager import ConnectorAuthManager
        status["email"] = ConnectorAuthManager().is_connected("gmail")
    except Exception:
        status["email"] = False
    try:
        from navig.messaging import adapter_setup
        from navig.messaging.adapter_registry import get_adapter_registry
        reg = get_adapter_registry()
        # The Deck is served by the Telegram worker, whose registry starts empty;
        # ensure_registered() builds the adapter from config exactly as a send
        # from this process would, so "configured" means "a send would work HERE".
        status["sms"] = bool(prefs.get_target("sms")) and adapter_setup.ensure_registered("sms")[0]
        status["discord"] = reg.is_available("discord") and bool(prefs.get_target("discord"))
        status["whatsapp"] = bool(prefs.get_target("whatsapp")) and (
            reg.is_available("whatsapp_cloud") or adapter_setup.ensure_registered("whatsapp")[0]
        )
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
            "telegram_routes": telegram_routes.all_routes(),
            # The noise report ("how much is NAVIG sending?") and the per-channel
            # delivery health ("did the last one actually go out?").
            "noise": feed.stats(days=7),
            "health": health.snapshot(),
        })
    except Exception as exc:
        logger.exception("notify prefs get failed")
        return _err(str(exc), 500)


async def handle_notify_prefs_post(request: "web.Request") -> "web.Response":
    body = await _body(request)
    try:
        # Matrix cell: {type, channel, enabled}
        if "type" in body and "channel" in body:
            prefs.set_cell(str(body["type"]), str(body["channel"]), _truthy(body.get("enabled")))
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

        if channel:
            # One channel → test THAT channel, whatever the matrix routes there.
            return _ok(await get_notification_router().send_test(str(channel)))
        result = await get_notification_router().dispatch(
            "custom",
            "Test notification",
            f"This is a test{' to ' + channel if channel else ''} from navig.",
            priority="normal",
            only_channels=[channel] if channel else None,
        )
        return _ok(result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("notify test failed")
        return _err(str(exc), 500)


async def handle_notify_telegram_route(request: "web.Request") -> "web.Response":
    """Per-type Telegram chat + sound.

    Body: ``{type, chat?, silent?, action?}``
      * ``chat`` — a chat id / ``@username`` to route to (resolved and checked with
        Telegram before saving), or ``""``/``null`` to send the type back to the DM.
      * ``silent`` — ``true`` (no sound) / ``false`` (rings) / ``null`` (the default).
      * ``action: "test"`` — post a test for this type where it is routed now.
    Returns the type's route ``{chat_id, title, silent, silent_default}``.
    """
    import asyncio

    body = await _body(request)
    type_key = str(body.get("type") or "")
    if not prefs._is_known_type(type_key):
        return _err("unknown notification type", 400)
    try:
        if body.get("action") == "test":
            from navig.notify.router import format_telegram_html
            from navig.notify.types import NOTIFICATION_TYPES

            label = next((t["label"] for t in NOTIFICATION_TYPES if t["key"] == type_key), type_key)
            text = format_telegram_html(
                type_key,
                f"Test — {label}",
                f"{label} notifications will arrive here. Set this chat's sound now if you want one.",
            )
            ok, detail = await asyncio.to_thread(
                telegram_routes.deliver, type_key, text,
                fallback_chat_id=telegram_routes.default_chat_id(),
            )
            return _ok({"ok": ok, "detail": detail})

        if "chat" in body:
            raw = str(body.get("chat") or "").strip()
            if raw:
                chat = await asyncio.to_thread(telegram_routes.resolve_chat, raw)
                telegram_routes.set_route(type_key, chat.chat_id, chat.title)
            else:
                telegram_routes.set_route(type_key, None)
        if "silent" in body:
            v = body.get("silent")
            telegram_routes.set_silent(type_key, None if v is None else bool(v))
        return _ok(telegram_routes.all_routes()[type_key])
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("notify telegram route failed")
        return _err(str(exc), 500)


async def handle_notify_pushover(request: "web.Request") -> "web.Response":
    """Pushover setup. Body: ``{app_token, user_key}`` to validate + save (the keys go
    to the vault, and Pushover is switched on for the recommended must-not-miss types
    the first time), or ``{action: "test", emergency?: bool}`` to send a test."""
    import asyncio

    from navig.notify import pushover

    body = await _body(request)
    try:
        if body.get("action") == "test":
            emergency = bool(body.get("emergency"))
            out = await asyncio.to_thread(
                pushover.send,
                "NAVIG test",
                "Emergency test — tap Acknowledge to stop it repeating."
                if emergency else "Pushover is connected.",
                priority=2 if emergency else 0,
                sound_name=pushover.sound(),
            )
            return _ok({"ok": out.ok, "detail": out.detail})
        token = str(body.get("app_token") or "").strip()
        user = str(body.get("user_key") or "").strip()
        out = await asyncio.to_thread(pushover.validate, token, user)
        if not out.ok:
            return _err(f"Pushover: {out.detail}", 400)
        first_time = not pushover.is_configured()
        pushover.save_credentials(token, user)
        enabled: list[str] = []
        if first_time:
            for t in pushover.RECOMMENDED_TYPES:
                prefs.set_cell(t, "pushover", True)
                enabled.append(t)
        return _ok({"ok": True, "detail": out.detail, "devices": list(out.devices), "enabled": enabled})
    except Exception as exc:
        logger.exception("notify pushover failed")
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
        from navig.notify.stripe_events import RECOMMENDED_EVENTS

        rows = signals.list_sources()
        for r in rows:
            r["ingest_url"] = _ingest_url(r["name"])
        return _ok({
            "sources": rows,
            # The address depends only on the name, so a UI can show it BEFORE the
            # source exists — Stripe hands out the whsec_ only after the endpoint
            # (which needs this address) is created.
            "ingest_url_template": _ingest_url("{name}"),
            "presets": list_presets(),
            # What a provider source needs from its dashboard — the UI shows the
            # exact events to tick next to the URL to paste.
            "providers": [{"key": "stripe", "label": "Stripe", "events": list(RECOMMENDED_EVENTS)}],
        })
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
                provider=(body.get("provider") or None),
                secret=(body.get("secret") or None),
            )
            row["ingest_url"] = _ingest_url(row["name"])
            return _ok({"created": row})  # full secret — shown once
        if action == "update":
            def _opt(key: str) -> str | None:
                # Absent = leave alone; present (even "") = set/clear.
                return None if key not in body else str(body.get(key) or "")

            row = signals.update_source(
                name,
                notify_type=_opt("type"),
                priority=(body.get("priority") or None),
                title_tmpl=_opt("title_tmpl"),
                body_tmpl=_opt("body_tmpl"),
            )
            row["ingest_url"] = _ingest_url(row["name"])
            return _ok({"updated": row})
        if action == "remove":
            return _ok({"removed": signals.remove_source(name)})
        if action == "rotate":
            secret = signals.rotate_secret(name, (body.get("secret") or None))
            return _ok({"name": name, "secret": secret})
        if action == "enable":
            return _ok({"updated": signals.set_enabled(name, coerce_bool(body.get("enabled", True), default=True))})
        if action == "test":
            src = signals.get_source(name)
            if src is None:
                return _err("unknown_source", 404)
            nt, title, btext, prio, data = signals.render_test(src)
            data["_test"] = True
            from navig.notify import dispatch as notify_dispatch

            return _ok(await notify_dispatch(nt, title, btext, priority=prio, data=data))
        return _err("expected action add|update|remove|rotate|enable|test", 400)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("notify signals post failed")
        return _err(str(exc), 500)


# ── Monitors (local producers toggle) ────────────────────────────────────────

_MONITORS = [
    {"key": "self_errors",      "label": "NAVIG errors",       "desc": "Tell me when the daemon logs an error."},
    {"key": "config_incidents", "label": "Config rescues",     "desc": "Tell me when the config/identity layer had to heal itself (a wiped config, a restored or re-identified deck key) — the silent events that can take the bot offline."},
    {"key": "connectivity",     "label": "Brain reachability", "desc": "Tell me when the Lighthouse uplink drops or returns."},
    {"key": "resources",        "label": "Resource alerts",    "desc": "Disk / memory / CPU threshold alerts."},
    {"key": "host_checks",      "label": "Host health checks", "desc": "Per-host disk / memory / CPU / service threshold alerts for this machine and the hosts in monitors.host_checks.hosts — with an all-clear when the condition recovers."},
    {"key": "webcam",           "label": "Webcam (privacy)",   "desc": "Alert when an app starts using your camera."},
    {"key": "pushover_escalation", "label": "Pushover → SMS escalation", "desc": "Text you when a Pushover emergency is still unacknowledged after 15 minutes. Idle unless one is pending."},
    {"key": "lens_updates",     "label": "Lens updates",       "desc": "Once a day, check each Lens installed from a link for a new version and hold it for your review — nothing changes until you approve."},
    {"key": "browser_reaper",   "label": "Browser cleanup",    "desc": "Close automation browsers nobody is using any more. Named profiles and any browser with a visible window are never touched."},
    {"key": "breach",           "label": "Breach check",       "desc": "Scan your vault emails against leak dumps (CyberAIGen). Off until you connect a token; alerts carry masked passwords only."},
    {"key": "recall_ingest",    "label": "Recall reading",     "desc": "Every 5 minutes, read new Claude Code, Codex and shared-chat conversations into Recall so they are searchable. Needs recall reading on (navig recall enable); nothing leaves this machine."},
    {"key": "site_probes",      "label": "Site uptime & certs", "desc": "Probe the URLs in monitors.site_probes.urls for downtime and TLS certificate expiry (warning before the date, critical at crit days) — with an all-clear when they recover."},
    {"key": "creative_scan",    "label": "Creative suggestions", "desc": "Idle unless Creative Agent Mode is on: every 6 hours, look through your own operation history for grounded improvement proposals and surface them on the Dashboard. Standard mode scans nothing; nothing is built or published without you."},
]
_MONITOR_KEYS = {m["key"] for m in _MONITORS}

# Monitors shown ON when the operator has never toggled them. Mirrors
# NavigGateway.MONITORS_DEFAULT_ON (kept in sync by test_monitor_defaults_agree) so the
# card and the gateway never disagree about a monitor's default state.
_DEFAULT_ON = frozenset({"config_incidents", "pushover_escalation", "lens_updates", "creative_scan"})


def _truthy(v: object) -> bool:
    # Canonical coercion so `config set monitors.x.enabled on/ON/off/…` resolves
    # correctly — case-insensitively and identically to the gateway
    # (test_monitor_enabled_coercion_matches_the_deck). `"false"` is not on.
    from navig.core.coerce import coerce_bool

    return coerce_bool(v)


def _monitor_enabled(cfg, key: str) -> bool:
    """The effective enabled state: an explicit config value wins; otherwise the default."""
    raw = cfg.get(f"monitors.{key}.enabled")
    return _truthy(raw) if raw is not None else (key in _DEFAULT_ON)


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
    if key in ("resources", "host_checks"):
        # host_checks samples THIS machine's resources/services through the same
        # psutil collectors when `hosts` is empty (the default target set).
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

        # Ask the live gateway whether each monitor is ACTUALLY running (not just
        # enabled in config): a crashed monitor is enabled+available yet dead, and
        # reporting config intent alone would show it green. None when no gateway is
        # attached (the caller can't distinguish, so it shouldn't cry "stopped").
        gw = request.app.get("gateway") if hasattr(request, "app") else None
        running_of = getattr(gw, "is_monitor_running", None) if gw is not None else None

        out = []
        for m in _MONITORS:
            available, requirement = _monitor_availability(
                m["key"], mode=mode, is_win=is_win, has_psutil=has_psutil
            )
            running: bool | None = None
            if callable(running_of):
                try:
                    running = bool(running_of(m["key"]))
                except Exception:  # never fail the whole card on one probe
                    running = None
            out.append({
                **m,
                "enabled": _monitor_enabled(cfg, m["key"]),
                "available": available,
                "requirement": requirement,
                "running": running,
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

    enabled = _truthy(body.get("enabled"))
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
