"""Status command for NAVIG."""

from __future__ import annotations

import json
from typing import Any, Dict

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.tunnel import TunnelManager


def get_gateway_status() -> Dict[str, Any]:
    """Get gateway status if running."""
    try:
        import requests
        response = requests.get("http://localhost:8789/status", timeout=2)
        if response.status_code == 200:
            data = response.json()
            return {
                "running": True,
                "uptime": data.get("uptime_seconds"),
                "sessions": data.get("sessions", {}).get("active", 0),
                "heartbeat": data.get("heartbeat", {}),
                "cron": data.get("cron", {}),
            }
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return {"running": False}


def get_status_payload(options: Dict[str, Any]) -> Dict[str, Any]:
    config_manager = get_config_manager()

    active_host, active_host_source = config_manager.get_active_host(return_source=True)
    active_app, active_app_source = config_manager.get_active_app(return_source=True)

    tunnel_manager = TunnelManager(config_manager)
    tunnel_info = None
    tunnel_error = None
    try:
        if active_host:
            tunnel_info = tunnel_manager.get_tunnel_status(active_host)
    except Exception as e:
        tunnel_error = str(e)

    gateway = get_gateway_status()

    return {
        "schema_version": "1.0.0",
        "active": {
            "host": {"name": active_host, "source": active_host_source},
            "app": {"name": active_app, "source": active_app_source},
        },
        "tunnel": {
            "running": bool(tunnel_info),
            "info": tunnel_info,
            "error": tunnel_error,
        },
        "gateway": gateway,
    }


def format_uptime(seconds: float) -> str:
    """Format uptime in human-readable format."""
    if not seconds:
        return "unknown"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def show_status(options: Dict[str, Any]) -> None:
    payload = get_status_payload(options)

    if options.get("json"):
        ch.raw_print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if options.get("plain") or options.get("raw"):
        # A single-line summary for scripting.
        host = payload["active"]["host"]["name"] or ""
        app = payload["active"]["app"]["name"] or ""
        tunnel = "running" if payload["tunnel"]["running"] else "stopped"
        gateway = "running" if payload["gateway"]["running"] else "stopped"
        ch.raw_print(f"host={host} app={app} tunnel={tunnel} gateway={gateway}")
        return

    ch.header("NAVIG Status")
    host = payload["active"]["host"]["name"]
    app = payload["active"]["app"]["name"]
    show_all = options.get("all", False)

    # Active context
    ch.info(f"Active host: {host or 'None'}")
    ch.info(f"Active app:  {app or 'None'}")
    ch.dim("")

    # Tunnel status
    if payload["tunnel"]["error"]:
        ch.warning(f"Tunnel: error - {payload['tunnel']['error']}")
    elif payload["tunnel"]["running"]:
        info = payload["tunnel"]["info"] or {}
        port = info.get("local_port", "?")
        pid = info.get("pid", "?")
        ch.success(f"Tunnel: running (port {port}, PID {pid})")
    else:
        ch.dim("Tunnel: stopped")

    # Gateway status
    gw = payload["gateway"]
    if gw["running"]:
        uptime = format_uptime(gw.get("uptime"))
        sessions = gw.get("sessions", 0)
        ch.success(f"Gateway: running (uptime {uptime}, {sessions} sessions)")

        # Heartbeat info
        hb = gw.get("heartbeat", {})
        if hb.get("running"):
            if show_all:
                next_run = hb.get("next_run", "unknown")
                last_run = hb.get("last_run", "never")
                ch.dim(f"  Heartbeat: active (next: {next_run}, last: {last_run})")
            else:
                ch.dim("  Heartbeat: active")

        # Cron info
        cron = gw.get("cron", {})
        jobs = cron.get("jobs", 0)
        enabled = cron.get("enabled_jobs", jobs)
        if jobs > 0:
            if show_all:
                ch.dim(f"  Cron jobs: {jobs} ({enabled} enabled)")
            else:
                ch.dim(f"  Cron jobs: {jobs}")
    else:
        ch.dim("Gateway: stopped")
        if show_all:
            ch.dim("  (start with: navig start)")

    # Extended status
    if show_all:
        ch.dim("")
        ch.dim("Tip: Use 'navig status --json' for machine-readable output")
