"""
NAVIG Gateway CLI Commands

Commands for managing the autonomous agent gateway server.
"""

from typing import Any, Dict, Optional

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")
try:
    from loguru import logger as _logger
except ImportError:
    import logging as _log

    _logger = _log.getLogger(__name__)


def _gw_base_url() -> str:
    """Return the local gateway base URL from config (gateway.port / gateway.host)."""
    from navig.gateway.client import gateway_base_url

    return gateway_base_url()


def _gateway_request_headers() -> Dict[str, str]:
    """Return auth headers for gateway admin requests when configured."""
    from navig.gateway.client import gateway_request_headers

    return gateway_request_headers()


gateway_app = typer.Typer(
    name="gateway",
    help="Manage the autonomous agent gateway",
    no_args_is_help=True,
)


@gateway_app.command("start")
def gateway_start(
    port: Optional[int] = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to run gateway on (default: gateway.port from config, fallback 8789)",
    ),
    host: Optional[str] = typer.Option(
        None,
        "--host",
        help="Host to bind to (default: gateway.host from config, fallback 0.0.0.0)",
    ),
    background: bool = typer.Option(
        False, "--background", "-b", help="Run in background"
    ),
):
    """
    Start the autonomous agent gateway server.

    The gateway provides:
    - HTTP/WebSocket API for agent communication
    - Session persistence across restarts
    - Heartbeat-based health monitoring
    - Cron job scheduling
    - Multi-channel message routing

    Examples:
        navig gateway start
        navig gateway start --port 9000
        navig gateway start --background
    """
    import asyncio

    # Fill port/host from config if not explicitly passed
    try:
        from navig.config import get_config_manager

        _raw = get_config_manager()._load_global_config()
    except Exception as _e:
        _logger.debug(f"Could not load gateway start config: {_e}")
        _raw = {}
    _gw_cfg = _raw.get("gateway") or {}
    if port is None:
        port = int(_gw_cfg.get("port") or 8789)
    if host is None:
        host = str(_gw_cfg.get("host") or "0.0.0.0")

    ch.info(f"Starting NAVIG Gateway on {host}:{port}...")

    try:
        from navig.gateway import GatewayConfig, NavigGateway

        # Build config dict for GatewayConfig
        raw_config = {
            "gateway": {
                "enabled": True,
                "port": port,
                "host": host,
            }
        }

        gateway_config = GatewayConfig(raw_config)
        gateway = NavigGateway(config=gateway_config)

        if background:
            ch.warning("Background mode not yet implemented. Running in foreground.")

        asyncio.run(gateway.start())

    except KeyboardInterrupt:
        ch.info("Gateway stopped by user")
    except ImportError as e:
        ch.error(f"Missing dependency: {e}")
        ch.info("Install with: pip install aiohttp")
    except Exception as e:
        ch.error(f"Gateway error: {e}")


@gateway_app.command("stop")
def gateway_stop():
    """
    Stop the running gateway server.

    Sends a shutdown signal to the running gateway via its API.
    If the gateway is running in the foreground, use Ctrl+C instead.

    Examples:
        navig gateway stop
    """
    try:
        import requests

        _base = _gw_base_url()
        # First check if gateway is running
        try:
            health_response = requests.get(
                f"{_base}/health",
                headers=_gateway_request_headers(),
                timeout=2,
            )
            if health_response.status_code != 200:
                ch.warning("Gateway does not appear to be running")
                return
        except Exception:  # noqa: BLE001
            ch.warning("Gateway is not running")
            return

        # Try to stop via API
        try:
            response = requests.post(
                f"{_base}/shutdown",
                headers=_gateway_request_headers(),
                timeout=5,
            )
            if response.status_code == 200:
                ch.success("Gateway shutdown signal sent")
            else:
                ch.warning(f"Shutdown request returned status {response.status_code}")
                ch.info("If running in foreground, use Ctrl+C to stop")
        except requests.exceptions.ConnectionError:
            # Connection closed - gateway probably stopped
            ch.success("Gateway stopped")
        except Exception as e:
            ch.warning(f"Could not send shutdown signal: {e}")
            ch.info("If running in foreground, use Ctrl+C to stop")
            ch.info("Or kill the process manually: pkill -f 'navig gateway'")

    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")


@gateway_app.command("status")
def gateway_status(
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """Show config state and liveness for every gateway channel.

    Checks: Telegram · Matrix · Discord · WhatsApp · Email
    Each row shows:  configured | token/key present | daemon/API reachable
    """
    import json as _json
    import socket as _socket

    # ── Load global config ────────────────────────────────────────────────────
    raw_cfg: dict = {}
    try:
        from navig.config import get_config_manager

        raw_cfg = get_config_manager()._load_global_config()
    except Exception:  # noqa: BLE001
        pass

    # ── Gateway daemon check (local HTTP) ─────────────────────────────────────
    def _http_alive(
        url: str, timeout: float = 2.0, headers: Optional[Dict[str, str]] = None
    ) -> bool:
        try:
            import urllib.request

            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status < 500
        except Exception:  # noqa: BLE001
            return False

    def _port_alive(host: str, port: int) -> bool:
        try:
            with _socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    # ── Telegram ──────────────────────────────────────────────────────────────
    tg_cfg = raw_cfg.get("telegram", {})
    tg_token = bool(tg_cfg.get("bot_token"))
    tg_users = len(tg_cfg.get("allowed_users", []))
    tg_groups = len(tg_cfg.get("allowed_groups", []))
    tg_online = False
    if tg_token:
        try:
            import json as _j
            import urllib.request

            tok = tg_cfg["bot_token"]
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{tok}/getMe", timeout=5
            ) as r:
                tg_online = _j.load(r).get("ok", False)
        except Exception:  # noqa: BLE001
            pass

    # ── Matrix ────────────────────────────────────────────────────────────────
    mx_cfg = (raw_cfg.get("comms") or {}).get("matrix") or raw_cfg.get("matrix") or {}
    mx_token = bool(mx_cfg.get("access_token"))
    mx_hs = mx_cfg.get("homeserver_url", "http://localhost:6167")
    mx_online = _http_alive(mx_hs.rstrip("/") + "/_matrix/client/versions")

    # ── Discord ───────────────────────────────────────────────────────────────
    dc_cfg = raw_cfg.get("discord", {})
    dc_token = bool(dc_cfg.get("bot_token") or dc_cfg.get("token"))
    dc_online = False
    if dc_token:
        dc_online = _http_alive("https://discord.com/api/v10/gateway", timeout=4)

    # ── WhatsApp (mautrix bridge) ─────────────────────────────────────────────
    wa_port = 29318
    wa_running = _port_alive("localhost", wa_port)
    wa_cfg = raw_cfg.get("whatsapp") or raw_cfg.get("bridges", {}).get("whatsapp", {})
    wa_enabled = bool(wa_cfg.get("enabled") or wa_cfg.get("WHATSAPP_ENABLED"))

    # ── Email / SMTP ──────────────────────────────────────────────────────────
    em_cfg = raw_cfg.get("email") or raw_cfg.get("smtp") or {}
    em_configured = bool(
        em_cfg.get("smtp_host") or em_cfg.get("SMTP_HOST") or em_cfg.get("host")
    )
    em_port = int(em_cfg.get("smtp_port") or em_cfg.get("port") or 587)
    em_host = str(em_cfg.get("smtp_host") or em_cfg.get("host") or "")
    em_online = _port_alive(em_host, em_port) if em_host else False

    # ── Daemon/gateway API ────────────────────────────────────────────────────
    gw_port = int(raw_cfg.get("gateway", {}).get("port") or 8789)
    gw_live = _http_alive(
        f"http://localhost:{gw_port}/health",
        headers=_gateway_request_headers(),
    )

    channels = [
        {
            "channel": "Telegram",
            "configured": tg_token,
            "detail": (
                f"users={tg_users}  groups={tg_groups}"
                if tg_token
                else "bot_token missing"
            ),
            "reachable": tg_online,
        },
        {
            "channel": "Matrix",
            "configured": mx_token,
            "detail": mx_hs if mx_token else "access_token missing",
            "reachable": mx_online,
        },
        {
            "channel": "Discord",
            "configured": dc_token,
            "detail": "bot_token present" if dc_token else "bot_token missing",
            "reachable": dc_online,
        },
        {
            "channel": "WhatsApp",
            "configured": wa_enabled,
            "detail": f"bridge port {wa_port}" if wa_enabled else "not enabled",
            "reachable": wa_running,
        },
        {
            "channel": "Email/SMTP",
            "configured": em_configured,
            "detail": f"{em_host}:{em_port}" if em_configured else "smtp_host missing",
            "reachable": em_online,
        },
        {
            "channel": "Gateway API",
            "configured": True,
            "detail": f"localhost:{gw_port}",
            "reachable": gw_live,
        },
    ]

    if json_out:
        print(_json.dumps(channels, indent=2))
        return

    ch.console.print("\n[bold]Gateway Channel Status[/bold]\n")
    ch.console.print(f"  {'Channel':<14} {'Config':<10} {'Reachable':<12} {'Detail'}")
    ch.console.print("  " + "─" * 62)

    for c in channels:
        cfg_icon = "[green]✓[/green]" if c["configured"] else "[red]✗[/red]"
        live_icon = "[green]✓[/green]" if c["reachable"] else "[dim]─[/dim]"
        ch.console.print(
            f"  [cyan]{c['channel']:<14}[/cyan] {cfg_icon:<12} {live_icon:<14} [dim]{c['detail']}[/dim]"
        )

    ch.console.print()
    if not gw_live:
        ch.dim("  Gateway daemon not running — start with: navig gateway start")
    else:
        ch.dim(f"  Gateway running on port {gw_port}")


@gateway_app.command("test")
def gateway_test(
    channel: str = typer.Argument(
        "all",
        help="Channel to test: all | telegram | matrix | discord | email",
    ),
    target: str = typer.Option(
        "",
        "--target",
        "-t",
        help="Recipient: @username / chat_id for Telegram; room for Matrix; address for email",
    ),
    message: str = typer.Option(
        "🟢 NAVIG gateway smoke-test — all systems go",
        "--message",
        "-m",
        help="Message body to send",
    ),
) -> None:
    """Send a smoke-test message through one or all configured channels.

    Examples::

        navig gateway test telegram --target @username
        navig gateway test telegram --target @username --message "custom text"
        navig gateway test matrix   --target "#alerts:navig.local"
        navig gateway test all      --target @username
    """

    channels_to_test = ["telegram", "matrix"] if channel == "all" else [channel.lower()]

    results: list[dict] = []

    for ch_name in channels_to_test:
        ch.console.print(f"\n[bold]Testing [cyan]{ch_name}[/cyan]…[/bold]")

        if ch_name == "telegram":
            if not target:
                ch.warning("  --target required for Telegram (e.g. --target @username)")
                results.append(
                    {"channel": "telegram", "ok": False, "reason": "no target"}
                )
                continue
            from navig.commands.telegram import telegram_send as _tg_send

            try:
                _tg_send(
                    target=target,
                    message=message,
                    parse_mode="Markdown",
                    resolve_only=False,
                    host="",
                )
                results.append({"channel": "telegram", "ok": True})
            except SystemExit:
                results.append(
                    {"channel": "telegram", "ok": False, "reason": "send failed"}
                )

        elif ch_name == "matrix":
            from navig.commands.bridge import matrix_bridge_test_alert as _mx_test

            try:
                _mx_test(message=message)
                results.append({"channel": "matrix", "ok": True})
            except Exception as exc:
                ch.warning(f"  Matrix test failed: {exc}")
                results.append({"channel": "matrix", "ok": False, "reason": str(exc)})

        elif ch_name == "discord":
            ch.dim(
                "  Discord test not yet implemented — verify via Discord Dev Portal."
            )
            results.append(
                {"channel": "discord", "ok": None, "reason": "not implemented"}
            )

        elif ch_name == "email":
            ch.dim("  Email test: run navig email send --to example@domain.com")
            results.append(
                {"channel": "email", "ok": None, "reason": "not implemented"}
            )

        else:
            ch.warning(
                f"  Unknown channel '{ch_name}'. Choices: telegram | matrix | discord | email | all"
            )
            results.append({"channel": ch_name, "ok": False, "reason": "unknown"})

    # Summary
    ch.console.print("\n[bold]Results[/bold]")
    for r in results:
        icon = (
            "[green]✓[/green]"
            if r["ok"]
            else ("[dim]–[/dim]" if r["ok"] is None else "[red]✗[/red]")
        )
        detail = f"  [dim]{r.get('reason','')}[/dim]" if r.get("reason") else ""
        ch.console.print(f"  {icon} {r['channel']}{detail}")
    ch.console.print()


@gateway_app.command("session")
def gateway_session(
    action: str = typer.Argument("list", help="Action: list, show, clear"),
    session_key: str = typer.Argument(None, help="Session key (for show/clear)"),
):
    """
    Manage gateway sessions.

    Examples:
        navig gateway session list
        navig gateway session show agent:default:telegram:123
        navig gateway session clear agent:default:telegram:123
    """
    try:
        import requests

        _base = _gw_base_url()
        if action == "list":
            response = requests.get(
                f"{_base}/sessions",
                headers=_gateway_request_headers(),
                timeout=5,
            )
            if response.status_code == 200:
                sessions = response.json().get("sessions", [])
                if sessions:
                    ch.info(f"Active sessions ({len(sessions)}):")
                    for s in sessions:
                        ch.info(f"  • {s.get('key', 'unknown')}")
                else:
                    ch.info("No active sessions")
            else:
                ch.error(f"Failed to list sessions: {response.status_code}")

        elif action == "show" and session_key:
            response = requests.get(
                f"{_base}/sessions/{session_key}",
                headers=_gateway_request_headers(),
                timeout=5,
            )
            if response.status_code == 200:
                session = response.json()
                ch.info(f"Session: {session_key}")
                ch.console.print_json(data=session)
            else:
                ch.error(f"Session not found: {session_key}")

        elif action == "clear" and session_key:
            response = requests.delete(
                f"{_base}/sessions/{session_key}",
                headers=_gateway_request_headers(),
                timeout=5,
            )
            if response.status_code == 200:
                ch.success(f"Session cleared: {session_key}")
            else:
                ch.error(f"Failed to clear session: {response.status_code}")
        else:
            ch.error("Invalid action or missing session_key")
            ch.info("Usage: navig gateway session list|show|clear [session_key]")

    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__) or "Connection refused" in str(e):
            ch.error("Gateway is not running. Start with: navig gateway start")
        else:
            ch.error(f"Error: {e}")


# ============================================================================
# Interactive Menu Wrapper Functions
# ============================================================================
# These functions provide a consistent interface for the interactive menu system.
# Each wrapper calls the underlying Typer command with appropriate defaults.


def status_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway status command (interactive menu)."""
    gateway_status()


def start_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway start command (interactive menu)."""
    # Start in foreground mode for interactive use — port/host come from config
    gateway_start(port=None, host=None, background=False)


def stop_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway stop command (interactive menu)."""
    gateway_stop()


def session_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway session list command (interactive menu)."""
    gateway_session(action="list", session_key=None)
