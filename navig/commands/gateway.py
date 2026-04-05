"""
NAVIG Gateway CLI Commands

Commands for managing the autonomous agent gateway server.
"""

from typing import Any

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
    from navig.gateway_client import gateway_base_url

    return gateway_base_url()


def _gateway_request_headers() -> dict[str, str]:
    """Return auth headers for gateway admin requests when configured."""
    from navig.gateway_client import gateway_request_headers

    return gateway_request_headers()


def _gw_request(method: str, path: str, **kwargs):
    """Send an authenticated request to the local gateway."""
    from navig.gateway_client import gateway_request

    return gateway_request(method, path, **kwargs)


def _load_gateway_cli_defaults() -> tuple[int, str]:
    """Return gateway port/host from config with stable CLI fallbacks."""
    from navig.gateway_client import gateway_cli_defaults

    return gateway_cli_defaults()


gateway_app = typer.Typer(
    name="gateway",
    help="Manage the autonomous agent gateway",
    no_args_is_help=True,
)


@gateway_app.command("start")
def gateway_start(
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to run gateway on (default: gateway.port from config, fallback 8789)",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help="Host to bind to (default: gateway.host from config, fallback 0.0.0.0)",
    ),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
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
        _logger.debug("Could not load gateway start config: %s", _e)
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
    # Helper: try to stop a stray gateway process via PID file
    def _try_kill_by_pid() -> bool:
        """Check for a gateway PID file and kill the process if found. Returns True if killed."""
        import sys

        pid_candidates = []
        try:
            from pathlib import Path

            home = Path.home()
            pid_candidates = [
                home / ".navig" / "gateway.pid",
                home / ".navig" / "run" / "gateway.pid",
                Path("/tmp/navig-gateway.pid"),
            ]
        except Exception:  # noqa: BLE001
            pass

        for pid_file in pid_candidates:
            try:
                if pid_file.exists():
                    pid = int(pid_file.read_text().strip())
                    if sys.platform == "win32":
                        import subprocess

                        result = subprocess.run(
                            ["taskkill", "/PID", str(pid), "/F"],
                            capture_output=True,
                            timeout=5,
                        )
                        killed = result.returncode == 0
                    else:
                        import os
                        import signal

                        os.kill(pid, signal.SIGTERM)
                        killed = True
                    if killed:
                        try:
                            pid_file.unlink(missing_ok=True)
                        except Exception:  # noqa: BLE001
                            pass
                        return True
            except (ProcessLookupError, ValueError):
                try:
                    pid_file.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        return False

    try:
        import requests

        _base = _gw_base_url()
        # First check if gateway is running
        http_reachable = False
        try:
            health_response = requests.get(
                f"{_base}/health",
                headers=_gateway_request_headers(),
                timeout=2,
            )
            http_reachable = health_response.status_code == 200
        except Exception:  # noqa: BLE001
            pass

        if not http_reachable:
            # Try PID-based kill as fallback (handles daemon-spawned gateway)
            if _try_kill_by_pid():
                ch.success("Gateway stopped")
            else:
                ch.dim("Gateway is not running")
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
            # Connection closed — gateway stopped cleanly
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
    def _http_alive(url: str, timeout: float = 2.0, headers: dict[str, str] | None = None) -> bool:
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
            with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getMe", timeout=5) as r:
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
    em_configured = bool(em_cfg.get("smtp_host") or em_cfg.get("SMTP_HOST") or em_cfg.get("host"))
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
                f"users={tg_users}  groups={tg_groups}" if tg_token else "bot_token missing"
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
        help="Channel to test (all|telegram|matrix|discord|email)",
    ),
    target: str = typer.Option(
        "",
        "--target",
        "-t",
        help="Target recipient (@username|chat_id for Telegram, room for Matrix, address for email)",
    ),
    message: str = typer.Option(
        "🟢 NAVIG gateway smoke-test — all systems go",
        "--message",
        "-m",
        help="Message text to send during smoke test",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit with code 1 when any tested channel fails",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON summary",
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
        if not json_output:
            ch.console.print(f"\n[bold]Testing [cyan]{ch_name}[/cyan]…[/bold]")

        if ch_name == "telegram":
            if not target:
                if not json_output:
                    ch.warning("  --target required for Telegram (e.g. --target @username)")
                results.append({"channel": "telegram", "ok": False, "reason": "no target"})
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
            except Exception as exc:
                if not json_output:
                    ch.warning(f"  Telegram test failed: {exc}")
                results.append({"channel": "telegram", "ok": False, "reason": str(exc)})

        elif ch_name == "matrix":
            from navig.commands.bridge import matrix_bridge_test_alert as _mx_test

            try:
                _mx_test(message=message)
                results.append({"channel": "matrix", "ok": True})
            except Exception as exc:
                if not json_output:
                    ch.warning(f"  Matrix test failed: {exc}")
                results.append({"channel": "matrix", "ok": False, "reason": str(exc)})

        elif ch_name == "discord":
            if not json_output:
                ch.dim("  Discord test not yet implemented — verify via Discord Dev Portal.")
            results.append({"channel": "discord", "ok": None, "reason": "not implemented"})

        elif ch_name == "email":
            if not json_output:
                ch.dim("  Email test: run navig email send --to example@domain.com")
            results.append({"channel": "email", "ok": None, "reason": "not implemented"})

        else:
            if not json_output:
                ch.warning(
                    f"  Unknown channel '{ch_name}'. Choices: telegram | matrix | discord | email | all"
                )
            results.append({"channel": ch_name, "ok": False, "reason": "unknown"})

    failed = [r for r in results if r.get("ok") is False]
    if json_output:
        import json as _json

        payload = {
            "results": results,
            "summary": {
                "channels_tested": len(results),
                "failed": len(failed),
                "ok": len(failed) == 0,
            },
        }
        print(_json.dumps(payload, indent=2))
    else:
        ch.console.print("\n[bold]Results[/bold]")
        for r in results:
            icon = (
                "[green]✓[/green]"
                if r["ok"]
                else ("[dim]–[/dim]" if r["ok"] is None else "[red]✗[/red]")
            )
            detail = f"  [dim]{r.get('reason', '')}[/dim]" if r.get("reason") else ""
            ch.console.print(f"  {icon} {r['channel']}{detail}")
        ch.console.print()

    if strict and failed:
        raise typer.Exit(1)


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


def status_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway status command (interactive menu)."""
    gateway_status()


def start_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway start command (interactive menu)."""
    # Start in foreground mode for interactive use — port/host come from config
    gateway_start(port=None, host=None, background=False)


def stop_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway stop command (interactive menu)."""
    gateway_stop()


def session_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway session list command (interactive menu)."""
    gateway_session(action="list", session_key=None)


# ============================================================================
# BOT - TELEGRAM BOT LAUNCHER
# ============================================================================

bot_app = typer.Typer(
    help="Telegram bot and multi-channel agent launcher",
    invoke_without_command=True,
    no_args_is_help=False,
)


@bot_app.callback()
def bot_callback(ctx: typer.Context):
    """Bot commands - run without subcommand to start bot."""
    if ctx.invoked_subcommand is None:
        # Default action: start bot in direct mode
        ctx.invoke(bot_start)


@bot_app.command("start")
def bot_start(
    gateway: bool = typer.Option(
        False, "--gateway", "-g", help="Start with gateway (session persistence)"
    ),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Gateway port (default: gateway.port from config, fallback 8789)",
    ),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
):
    """
    Start the NAVIG Telegram bot.

    By default runs in direct mode (standalone).
    Use --gateway to start both gateway and bot together.

    Examples:
        navig bot                    # Start bot (direct mode)
        navig bot --gateway          # Start gateway + bot together
        navig bot -g -p 9000         # Gateway on custom port
    """
    import os
    import subprocess
    import sys

    # Check for telegram token (vault-first, env/config fallback)
    from navig.messaging.secrets import resolve_telegram_bot_token

    telegram_token = resolve_telegram_bot_token()
    if not telegram_token:
        ch.error("TELEGRAM_BOT_TOKEN not set!")
        ch.info("  Get token from @BotFather on Telegram")
        ch.info("  Add to .env file: TELEGRAM_BOT_TOKEN=your-token")
        raise typer.Exit(1)

    if gateway:
        if port is None:
            port, _host = _load_gateway_cli_defaults()
        ch.info("Starting NAVIG with Gateway + Telegram Bot...")
        ch.info(f"  Gateway: http://localhost:{port}")
        ch.info("  Bot: Telegram")
        cmd = [
            sys.executable,
            "-m",
            "navig.daemon.telegram_worker",
            "--port",
            str(port),
        ]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)
    else:
        ch.info("Starting NAVIG Telegram Bot (direct mode)...")
        ch.warning("⚠️  Conversations reset on bot restart")
        ch.info("   Use 'navig bot --gateway' for session persistence")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)


@bot_app.command("status")
def bot_status():
    """Check if bot is running."""
    import subprocess
    import sys

    patterns = r"navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start"

    try:
        if sys.platform == "win32":
            ps_cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\") "
                f"| Where-Object {{ $_.CommandLine -match '{patterns}' }} "
                "| Select-Object -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
            )
            pids = [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]
            if pids:
                ch.success("Bot appears to be running")
                ch.info(f"  PIDs: {', '.join(pids)}")
            else:
                ch.warning("Bot does not appear to be running")
        else:
            result = subprocess.run(["pgrep", "-f", patterns], capture_output=True, text=True)
            if result.returncode == 0:
                ch.success("Bot is running")
                ch.info(f"  PIDs: {result.stdout.strip()}")
            else:
                ch.warning("Bot is not running")
    except Exception as e:
        ch.error(f"Could not check status: {e}")


@bot_app.command("stop")
def bot_stop():
    """Stop all running NAVIG bot/gateway processes."""
    import subprocess
    import sys

    patterns = r"navig\.daemon\.telegram_worker|navig\.daemon\.entry|navig gateway start"

    try:
        if sys.platform == "win32":
            ps_cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\") "
                f"| Where-Object {{ $_.CommandLine -match '{patterns}' }} "
                "| Select-Object -ExpandProperty ProcessId"
            )
            find_result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
            )
            pids = [
                line.strip() for line in find_result.stdout.splitlines() if line.strip().isdigit()
            ]
            if not pids:
                ch.warning("No running processes found")
                return
            for pid in pids:
                subprocess.run(
                    ["taskkill", "/PID", pid, "/T", "/F"],
                    capture_output=True,
                    text=True,
                )
            ch.success(f"Stopped NAVIG bot/gateway processes: {', '.join(pids)}")
        else:
            result = subprocess.run(["pkill", "-f", patterns], capture_output=True, text=True)
            if result.returncode == 0:
                ch.success("Stopped NAVIG bot/gateway")
            else:
                ch.warning("No running processes found")
    except Exception as e:
        ch.error(f"Error stopping: {e}")


# ============================================================================
# HEARTBEAT - PERIODIC HEALTH CHECKS
# ============================================================================

heartbeat_app = typer.Typer(
    help="Periodic health check system",
    invoke_without_command=True,
    no_args_is_help=False,
)


@heartbeat_app.callback()
def heartbeat_callback(ctx: typer.Context):
    """Heartbeat commands - run without subcommand for help."""
    from navig.cli._callbacks import show_subcommand_help

    if ctx.invoked_subcommand is None:
        show_subcommand_help("heartbeat", ctx)
        raise typer.Exit()


@heartbeat_app.command("status")
def heartbeat_status():
    """Show heartbeat status."""
    from datetime import datetime

    import requests

    try:
        response = _gw_request("GET", "/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            hb = data.get("heartbeat", {})
            config = data.get("config", {})

            if hb.get("running"):
                ch.success("Heartbeat is running")

                interval = config.get("heartbeat_interval", "30m")
                ch.info(f"  Interval: {interval}")

                next_run = hb.get("next_run")
                if next_run:
                    try:
                        next_dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                        now = datetime.now(next_dt.tzinfo) if next_dt.tzinfo else datetime.now()
                        diff = next_dt - now
                        minutes = int(diff.total_seconds() / 60)
                        if minutes > 0:
                            ch.info(f"  Next check: in {minutes} minutes")
                        else:
                            ch.info("  Next check: imminent")
                    except Exception:
                        ch.info(f"  Next check: {next_run}")
                else:
                    ch.info("  Next check: unknown")

                last_run = hb.get("last_run")
                if last_run:
                    ch.info(f"  Last run: {last_run}")
                else:
                    ch.info("  Last run: never")
            else:
                ch.warning("Heartbeat is not running")
                ch.info("Start gateway to enable heartbeat: navig gateway start")
        else:
            ch.error(f"Failed to get status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("trigger")
def heartbeat_trigger():
    """Trigger an immediate heartbeat check."""
    import requests

    ch.info("Triggering heartbeat check...")

    try:
        response = _gw_request("POST", "/heartbeat/trigger", timeout=300)
        if response.status_code == 200:
            result = response.json()
            if result.get("suppressed"):
                ch.success("HEARTBEAT_OK - All systems healthy")
            elif result.get("issues"):
                ch.warning(f"Issues found: {len(result['issues'])}")
                for issue in result["issues"]:
                    ch.warning(f"  • {issue}")
            else:
                ch.success("Heartbeat completed")
        else:
            ch.error(f"Heartbeat failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("history")
def heartbeat_history(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of entries to show"),
):
    """Show heartbeat history."""
    import requests

    try:
        response = _gw_request("GET", f"/heartbeat/history?limit={limit}", timeout=5)
        if response.status_code == 200:
            history = response.json().get("history", [])
            if history:
                ch.info(f"Heartbeat history (last {len(history)}):")
                for entry in history:
                    status = "✅" if entry.get("success") else "❌"
                    suppressed = " (OK)" if entry.get("suppressed") else ""
                    ch.info(
                        f"  {status} {entry.get('timestamp', '?')}{suppressed} - {entry.get('duration', 0):.1f}s"
                    )
            else:
                ch.info("No heartbeat history")
        else:
            ch.error(f"Failed to get history: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@heartbeat_app.command("configure")
def heartbeat_configure(
    interval: int = typer.Option(None, "--interval", "-i", help="Interval in minutes"),
    enable: bool = typer.Option(None, "--enable/--disable", help="Enable/disable heartbeat"),
):
    """Configure heartbeat settings."""
    from navig.config import ConfigManager

    config_manager = ConfigManager()

    if interval is not None or enable is not None:
        config = config_manager.global_config
        if "heartbeat" not in config:
            config["heartbeat"] = {}

        if interval is not None:
            config["heartbeat"]["interval"] = interval
            ch.success(f"Set heartbeat interval to {interval} minutes")

        if enable is not None:
            config["heartbeat"]["enabled"] = enable
            ch.success(f"Heartbeat {'enabled' if enable else 'disabled'}")

        config_manager.save_global()
    else:
        config = config_manager.global_config
        hb = config.get("heartbeat", {})
        ch.info("Heartbeat configuration:")
        ch.info(f"  Enabled: {hb.get('enabled', True)}")
        ch.info(f"  Interval: {hb.get('interval', 30)} minutes")
        ch.info(f"  Timeout: {hb.get('timeout', 300)} seconds")


# ============================================================================
# APPROVAL SYSTEM (Human-in-the-loop for agent actions)
# ============================================================================

approve_app = typer.Typer(
    help="Human approval system for agent actions",
    invoke_without_command=True,
    no_args_is_help=False,
)


@approve_app.callback()
def approve_callback(ctx: typer.Context):
    """Approval management - run without subcommand to list pending."""
    if ctx.invoked_subcommand is None:
        approve_list()


@approve_app.command("list")
def approve_list():
    """List pending approval requests."""
    import requests

    try:
        response = _gw_request("GET", "/approval/pending", timeout=5)
        if response.status_code == 200:
            data = response.json()
            pending = data.get("pending", [])

            if not pending:
                ch.info("No pending approval requests")
                return

            ch.info(f"Pending approval requests ({len(pending)}):")
            for req in pending:
                level_color = {
                    "confirm": "yellow",
                    "dangerous": "red",
                    "never": "bright_red",
                }.get(req.get("level", ""), "white")

                ch.console.print(
                    f"  [{req['id']}] {req['action']} ({req['level']}) - {req.get('description', '')}",
                    style=level_color,
                )
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("yes")
def approve_yes(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Approve a pending request."""
    import requests

    try:
        response = _gw_request(
            "POST",
            f"/approval/{request_id}/respond",
            json={"approved": True, "reason": reason},
            timeout=5,
        )
        if response.status_code == 200:
            ch.success(f"Request {request_id} approved")
        elif response.status_code == 404:
            ch.error(f"Request {request_id} not found")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("no")
def approve_no(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Deny a pending request."""
    import requests

    try:
        response = _gw_request(
            "POST",
            f"/approval/{request_id}/respond",
            json={"approved": False, "reason": reason},
            timeout=5,
        )
        if response.status_code == 200:
            ch.success(f"Request {request_id} denied")
        elif response.status_code == 404:
            ch.error(f"Request {request_id} not found")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@approve_app.command("policy")
def approve_policy():
    """Show approval policy (patterns and levels)."""
    try:
        from navig.approval import ApprovalPolicy

        policy = ApprovalPolicy.default()

        ch.info("Approval Policy Patterns:")
        ch.console.print("\n[bold green]SAFE (no approval needed):[/bold green]")
        for pattern in policy.patterns.get("safe", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold yellow]CONFIRM (requires approval):[/bold yellow]")
        for pattern in policy.patterns.get("confirm", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold red]DANGEROUS (always confirm):[/bold red]")
        for pattern in policy.patterns.get("dangerous", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold bright_red]NEVER (always denied):[/bold bright_red]")
        for pattern in policy.patterns.get("never", []):
            ch.console.print(f"  • {pattern}")
    except ImportError:
        ch.error("Approval module not available")
    except Exception as e:
        ch.error(f"Error: {e}")


# ============================================================================
# TASK QUEUE (Async operations queue)
# ============================================================================

queue_app = typer.Typer(
    help="Task queue for async operations",
    invoke_without_command=True,
    no_args_is_help=False,
)


@queue_app.callback()
def queue_callback(ctx: typer.Context):
    """Task queue - run without subcommand to list tasks."""
    if ctx.invoked_subcommand is None:
        queue_list()


@queue_app.command("list")
def queue_list(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max tasks to show"),
):
    """List queued tasks."""
    import requests

    try:
        params = {"limit": limit}
        if status:
            params["status"] = status

        response = _gw_request("GET", "/tasks", params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            tasks = data.get("tasks", [])

            if not tasks:
                ch.info("No tasks in queue")
                return

            ch.info(f"Tasks ({len(tasks)}):")
            for task in tasks:
                status_color = {
                    "pending": "blue",
                    "queued": "cyan",
                    "running": "yellow",
                    "completed": "green",
                    "failed": "red",
                    "cancelled": "dim",
                }.get(task.get("status", ""), "white")

                ch.console.print(
                    f"  [{task['id']}] {task['name']} - {task['status']}",
                    style=status_color,
                )
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("add")
def queue_add(
    name: str = typer.Argument(..., help="Task name"),
    handler: str = typer.Argument(..., help="Handler to execute"),
    params: str | None = typer.Option(None, "--params", "-p", help="JSON params"),
    priority: int = typer.Option(50, "--priority", help="Priority (lower = higher)"),
):
    """Add a task to the queue."""
    import json as json_mod

    import requests

    try:
        task_params = {}
        if params:
            task_params = json_mod.loads(params)

        response = _gw_request(
            "POST",
            "/tasks",
            json={
                "name": name,
                "handler": handler,
                "params": task_params,
                "priority": priority,
            },
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            ch.success(f"Task added: {data.get('id')}")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except json_mod.JSONDecodeError:
        ch.error("Invalid JSON in --params")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("show")
def queue_show(
    task_id: str = typer.Argument(..., help="Task ID"),
):
    """Show task details."""
    import requests

    try:
        response = _gw_request("GET", f"/tasks/{task_id}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            ch.info(f"Task: {data.get('name', 'unknown')}")
            ch.console.print(f"  ID: {data.get('id')}")
            ch.console.print(f"  Handler: {data.get('handler')}")
            ch.console.print(f"  Status: {data.get('status')}")
            ch.console.print(f"  Priority: {data.get('priority')}")
            if data.get("error"):
                ch.console.print(f"  Error: {data.get('error')}", style="red")
            if data.get("result"):
                ch.console.print(f"  Result: {data.get('result')}")
        elif response.status_code == 404:
            ch.error(f"Task {task_id} not found")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("cancel")
def queue_cancel(
    task_id: str = typer.Argument(..., help="Task ID to cancel"),
):
    """Cancel a pending task."""
    import requests

    try:
        response = _gw_request("POST", f"/tasks/{task_id}/cancel", timeout=5)
        if response.status_code == 200:
            ch.success(f"Task {task_id} cancelled")
        elif response.status_code == 404:
            ch.error(f"Task {task_id} not found")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")


@queue_app.command("stats")
def queue_stats():
    """Show queue statistics."""
    import requests

    try:
        response = _gw_request("GET", "/tasks/stats", timeout=5)
        if response.status_code == 200:
            data = response.json()

            ch.info("Task Queue Statistics:")
            ch.console.print(f"  Total tasks: {data.get('total_tasks', 0)}")
            ch.console.print(f"  Heap size: {data.get('heap_size', 0)}")
            ch.console.print(f"  Completed: {data.get('completed_count', 0)}")

            counts = data.get("status_counts", {})
            if counts:
                ch.console.print("\n  Status breakdown:")
                for status, count in counts.items():
                    ch.console.print(f"    {status}: {count}")

            worker = data.get("worker", {})
            if worker:
                ch.console.print("\n  Worker:")
                ch.console.print(f"    Running: {worker.get('running', False)}")
                ch.console.print(f"    Active tasks: {worker.get('active_tasks', 0)}")
                ch.console.print(f"    Completed: {worker.get('tasks_completed', 0)}")
                ch.console.print(f"    Failed: {worker.get('tasks_failed', 0)}")
        elif response.status_code == 503:
            ch.warning("Tasks module not available")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        ch.warning("Gateway is not running")
    except Exception as e:
        ch.error(f"Error: {e}")
