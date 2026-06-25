"""Cloud-tunnel handlers for the Deck API.

Exposes the local :class:`navig.cloud.CloudManager` state so the Deck UI can
show "Cloud: online · https://abc-def.trycloudflare.com · last beat 12s ago"
and lets the user toggle ``cloud.enabled`` from the Account -> Cloud subpage.

Routes:
    GET  /api/deck/cloud/status   -> snapshot of CloudManager state
    POST /api/deck/cloud/enabled  -> flip cloud.enabled (body: {enabled: bool})
    POST /api/deck/cloud/restart  -> stop+start the CloudManager in-place
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _gateway_from_request(request: "web.Request"):
    """Returns the NavigGateway instance attached to the running app."""
    return request.app.get("gateway") or request.app.get("navig_gateway")


def _config():
    from navig.core import Config
    return Config()


async def handle_deck_cloud_status(request: "web.Request") -> "web.Response":
    """Return a snapshot of CloudManager state + the persisted config flag.

    Safe to call when the manager is not running -- ``status`` will be ``off``
    and ``tunnel_url`` will be null.
    """
    cfg = _config()
    # Default ON to match _start_cloud_manager's behaviour. The previous
    # default of False here meant the Settings -> Cloud toggle rendered
    # "Enable cloud" while the manager was happily online -- confusing UX.
    enabled = bool(cfg.get("cloud.enabled", True))
    broker_url = cfg.get("cloud.broker_url", "https://api.navig.run")
    public_url = (cfg.get("cloud.public_url") or "").strip()

    payload: dict[str, Any] = {
        "enabled": enabled,
        "broker_url": broker_url,
        "public_url": public_url,
        "mode": "direct" if public_url else "tunnel",
        "status": "off",
        "tunnel_url": None,
        "last_heartbeat_at": None,
        "last_error": None,
        "rotations": 0,
        "label": cfg.get("cloud.tunnel_label", "") or "",
        # Relay-gate fields. Direct mode is always allowed; tunnel mode
        # is subscription-gated. The Deck reads these to render the
        # "subscription required" / "lapsed grace" banner and decide
        # whether to offer the "enable relay" toggle.
        "relay_available": True,
        "relay_reason": "direct_mode" if public_url else "solo_free",
        "relay_banner": None,
        "relay_grace_days_left": None,
    }

    gw = _gateway_from_request(request)

    # Re-evaluate the gate live for the tunnel path so the Deck reflects
    # the current license state without waiting for a daemon restart.
    if not public_url:
        try:
            from navig.license import current_status
            from navig.license.relay_gate import evaluate_relay_access
            decision = evaluate_relay_access(current_status())
            payload["relay_available"] = decision.allowed
            payload["relay_reason"] = decision.reason
            payload["relay_banner"] = decision.banner
            payload["relay_grace_days_left"] = decision.grace_days_left
        except Exception as exc:  # noqa: BLE001
            logger.debug("relay gate evaluation in /cloud/status failed: %r", exc)

    cm = getattr(gw, "cloud_manager", None) if gw is not None else None
    if cm is not None:
        try:
            snap = cm.snapshot()
            payload.update(snap)
            # Source of truth: a live manager means cloud IS enabled, even if
            # the user's config file still has the old default.
            if snap.get("status") in ("online", "starting"):
                payload["enabled"] = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("cloud_manager.snapshot failed: %r", exc)
    return web.json_response(payload)


async def handle_deck_cloud_enabled(request: "web.Request") -> "web.Response":
    """Flip cloud.enabled and start/stop the manager in-place.

    Body: ``{"enabled": true|false}``. The change is persisted to
    ``~/.navig/config.yaml`` so it survives gateway restarts.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(body, dict) or "enabled" not in body:
        return web.json_response({"ok": False, "error": "missing_enabled"}, status=400)
    desired = bool(body.get("enabled"))

    cfg = _config()
    cfg.set("cloud.enabled", desired, scope="global")
    cfg.save(scope="global")

    gw = _gateway_from_request(request)
    if gw is None:
        return web.json_response({"ok": True, "enabled": desired, "manager": "no_gateway"})

    cm = getattr(gw, "cloud_manager", None)

    if desired:
        if cm is not None and cm.status in ("online", "starting"):
            return web.json_response({"ok": True, "enabled": True, "manager": "already_running"})
        # Pre-check the relay gate so we return a clear 402 instead of
        # silently starting + then having _start_cloud_manager swallow it.
        # Direct mode (public_url set) bypasses the gate.
        public_url = (cfg.get("cloud.public_url") or "").strip()
        if not public_url:
            try:
                from navig.license import current_status
                from navig.license.relay_gate import evaluate_relay_access
                decision = evaluate_relay_access(current_status())
                if not decision.allowed:
                    return web.json_response(
                        {
                            "ok": False,
                            "error": "relay_unavailable",
                            "relay_reason": decision.reason,
                            "relay_banner": decision.banner,
                        },
                        status=402,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("relay gate pre-check failed: %r; allowing", exc)
        # Spawn one in-place. _start_cloud_manager re-reads config + handles errors.
        try:
            await gw._start_cloud_manager()
        except Exception as exc:  # noqa: BLE001
            return web.json_response(
                {"ok": False, "error": f"start_failed: {exc}"}, status=500
            )
        return web.json_response({"ok": True, "enabled": True, "manager": "started"})

    # disable
    if cm is None:
        return web.json_response({"ok": True, "enabled": False, "manager": "was_off"})
    try:
        await cm.stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cloud_manager.stop failed: %r", exc)
    gw.cloud_manager = None
    return web.json_response({"ok": True, "enabled": False, "manager": "stopped"})


async def handle_deck_cloud_restart(request: "web.Request") -> "web.Response":
    """Restart the CloudManager in-place. Useful after rotating cloudflared_path."""
    gw = _gateway_from_request(request)
    if gw is None:
        return web.json_response({"ok": False, "error": "no_gateway"}, status=500)
    cm = getattr(gw, "cloud_manager", None)
    if cm is not None:
        try:
            await cm.stop()
        except Exception as exc:  # noqa: BLE001
            logger.debug("restart: stop raised %r", exc)
        gw.cloud_manager = None
    try:
        await gw._start_cloud_manager()
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"ok": False, "error": f"start_failed: {exc}"}, status=500)
    new_cm = getattr(gw, "cloud_manager", None)
    return web.json_response({
        "ok": True,
        "status": (new_cm.status if new_cm else "off"),
        "tunnel_url": (new_cm.current_url if new_cm else None),
    })
