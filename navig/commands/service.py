"""
NAVIG Service CLI Commands

Manage NAVIG as a persistent background service.

Commands:
    navig service install   — Install as Windows service / scheduled task
    navig service start     — Start the daemon
    navig service stop      — Stop the daemon
    navig service restart   — Restart the daemon
    navig service status    — Show daemon and child process status
    navig service uninstall — Remove the service
    navig service logs      — Tail daemon logs
    navig service config    — Show/edit daemon configuration
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from navig.lazy_loader import lazy_import
from navig.platform.paths import config_dir

ch = lazy_import("navig.console_helper")

service_app = typer.Typer(
    name="service",
    help="Manage NAVIG as a persistent background service (daemon)",
    no_args_is_help=True,
)


# =========================================================================
# install
# =========================================================================
@service_app.command("install")
def service_install(
    method: str | None = typer.Option(
        None,
        "--method",
        "-m",
        help="Installation method: 'systemd' (Linux), 'nssm' (Windows service), or 'task' (Task Scheduler). Auto-detected if omitted.",
    ),
    no_start: bool = typer.Option(
        False,
        "--no-start",
        help="Install but don't start the daemon yet",
    ),
    bot: bool = typer.Option(True, "--bot/--no-bot", help="Include Telegram bot"),
    gateway: bool = typer.Option(False, "--gateway/--no-gateway", help="Include gateway server"),
    scheduler: bool = typer.Option(
        False, "--scheduler/--no-scheduler", help="Include cron scheduler"
    ),
    health_port: int = typer.Option(
        0, "--health-port", help="TCP health-check port (0 = disabled)"
    ),
):
    """
    Install NAVIG daemon as a persistent service.

    Automatically picks the best method for the current platform:
      Linux:   systemd user service (or system-wide if root)
      Windows: NSSM (if installed + admin), else Task Scheduler

    Examples:
        navig service install
        navig service install --method systemd
        navig service install --method task
        navig service install --method nssm
        navig service install --gateway --scheduler
    """
    # Save config
    import json

    from navig.daemon import service_manager as sm
    from navig.daemon.entry import save_default_config

    config_path = save_default_config()
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cfg["telegram_bot"] = bot
    cfg["gateway"] = gateway
    cfg["scheduler"] = scheduler
    cfg["health_port"] = health_port
    config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    ch.info("Installing NAVIG daemon...")
    chosen = method or sm.detect_best_method()
    ch.console.print(f"  Method: {chosen.upper()}")
    ch.console.print(f"  Bot: {'yes' if bot else 'no'}")
    ch.console.print(f"  Gateway: {'yes' if gateway else 'no'}")
    ch.console.print(f"  Scheduler: {'yes' if scheduler else 'no'}")
    ch.console.print()

    ok, msg = sm.install(method=chosen, start_now=not no_start)
    if ok:
        ch.success(msg)
        ch.console.print()
        ch.info("Manage with:")
        ch.console.print("  navig service status")
        ch.console.print("  navig service stop")
        ch.console.print("  navig service logs")
    else:
        ch.error(msg)
        if "admin" in msg.lower() or "nssm" in msg.lower():
            ch.console.print()
            ch.info("Tip: Use --method task to install without admin privileges")
            ch.console.print("  navig service install --method task")
        raise typer.Exit(1)


# =========================================================================
# start (foreground or signal running daemon)
# =========================================================================
@service_app.command("start")
def service_start(
    foreground: bool = typer.Option(
        False,
        "--foreground",
        "-f",
        help="Run in foreground (blocks terminal)",
    ),
):
    """
    Start the NAVIG daemon.

    Without --foreground, starts the daemon as a background process.
    With --foreground, blocks the terminal (useful for debugging).

    Examples:
        navig service start
        navig service start --foreground
    """
    from navig.daemon.supervisor import NavigDaemon

    if NavigDaemon.is_running():
        pid = NavigDaemon.read_pid()
        ch.info(f"Daemon already running (pid={pid})")
        return

    if foreground:
        ch.info("Starting NAVIG daemon in foreground (Ctrl+C to stop)...")
        from navig.daemon.entry import main as daemon_main

        daemon_main()
    else:
        import subprocess

        from navig.daemon.service_manager import _pythonw_exe

        # Use pythonw.exe on Windows — completely invisible, no console window
        exe = _pythonw_exe()
        cmd = [exe, "-m", "navig.daemon.entry"]
        ch.info("Starting NAVIG daemon in background...")
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
        import time

        time.sleep(2)
        if NavigDaemon.is_running():
            ch.success(f"Daemon started (pid={NavigDaemon.read_pid()})")
        else:
            ch.error("Daemon failed to start. Check logs: navig service logs")
            raise typer.Exit(1)


# =========================================================================
# stop
# =========================================================================
@service_app.command("stop")
def service_stop():
    """
    Stop the running NAVIG daemon.

    Sends a graceful shutdown signal. Children are stopped first.

    Examples:
        navig service stop
    """
    from navig.daemon.supervisor import NavigDaemon

    if not NavigDaemon.is_running():
        ch.info("Daemon is not running")
        return

    pid = NavigDaemon.read_pid()
    ch.info(f"Stopping daemon (pid={pid})...")
    if NavigDaemon.stop_running_daemon():
        ch.success("Daemon stopped")
    else:
        ch.error("Failed to stop daemon. Try: taskkill /F /PID " + str(pid))
        raise typer.Exit(1)


# =========================================================================
# restart
# =========================================================================
@service_app.command("restart")
def service_restart():
    """
    Restart the NAVIG daemon.

    Examples:
        navig service restart
    """
    import subprocess
    import time

    from navig.daemon.supervisor import NavigDaemon

    if NavigDaemon.is_running():
        ch.info("Stopping daemon...")
        NavigDaemon.stop_running_daemon()
        time.sleep(1)

    ch.info("Starting daemon...")
    from navig.daemon.service_manager import _pythonw_exe

    exe = _pythonw_exe()
    cmd = [exe, "-m", "navig.daemon.entry"]
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
    time.sleep(2)
    if NavigDaemon.is_running():
        ch.success(f"Daemon restarted (pid={NavigDaemon.read_pid()})")
    else:
        ch.error("Daemon failed to start. Check: navig service logs")


# =========================================================================
# status
# =========================================================================
@service_app.command("status")
def service_status(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show NAVIG daemon status.

    Examples:
        navig service status
        navig service status --json
    """
    import json

    from navig.daemon import service_manager as sm
    from navig.daemon.supervisor import NavigDaemon

    if json_output:
        state = NavigDaemon.read_state()
        running = NavigDaemon.is_running()
        out = {
            "running": running,
            "pid": NavigDaemon.read_pid(),
            "children": state.get("children", []) if state else [],
        }
        print(json.dumps(out, indent=2))
        return

    running, detail = sm.status()
    ch.console.print()
    if running:
        ch.success("NAVIG Daemon is RUNNING")
    else:
        ch.info("NAVIG Daemon is STOPPED")
    ch.console.print()
    for line in detail.split("\n"):
        ch.console.print(f"  {line}")
    ch.console.print()


# =========================================================================
# uninstall
# =========================================================================
@service_app.command("uninstall")
def service_uninstall(
    method: str | None = typer.Option(None, "--method", "-m", help="nssm, task, or systemd"),
):
    """
    Remove NAVIG daemon service.

    Examples:
        navig service uninstall
        navig service uninstall --method systemd
        navig service uninstall --method task
    """
    from navig.daemon import service_manager as sm
    from navig.daemon.supervisor import NavigDaemon

    # Stop first
    if NavigDaemon.is_running():
        ch.info("Stopping daemon...")
        NavigDaemon.stop_running_daemon()

    chosen = method or sm.detect_best_method()
    ok, msg = sm.uninstall(method=chosen)
    if ok:
        ch.success(msg)
    else:
        ch.error(msg)
        raise typer.Exit(1)


# =========================================================================
# logs
# =========================================================================
@service_app.command("logs")
def service_logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    child: str | None = typer.Option(
        None, "--child", "-c", help="Show specific child log (e.g. 'children')"
    ),
):
    """
    Show NAVIG daemon logs.

    Examples:
        navig service logs
        navig service logs -f
        navig service logs -n 100
        navig service logs --child children
    """
    log_dir = config_dir() / "logs"

    if child:
        log_file = log_dir / f"{child}.log"
    else:
        log_file = log_dir / "daemon.log"

    if not log_file.exists():
        ch.info(f"No log file found: {log_file}")
        ch.info("Start the daemon first: navig service start")
        return

    if follow:
        ch.info(f"Following {log_file.name} (Ctrl+C to stop)...")
        import time

        with open(log_file, encoding="utf-8", errors="replace") as f:
            # Go to end
            f.seek(0, 2)
            try:
                while True:
                    line = f.readline()
                    if line:
                        print(line.rstrip())
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                pass  # user interrupted; clean exit
    else:
        # Read last N lines
        try:
            all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in all_lines[-lines:]:
                print(line)
        except Exception as e:
            ch.error(f"Error reading log: {e}")


# =========================================================================
# config
# =========================================================================
@service_app.command("config")
def service_config(
    show: bool = typer.Option(True, "--show/--edit", help="Show or edit config"),
):
    """
    Show or manage daemon configuration.

    Config file: ~/.navig/daemon/config.json

    Examples:
        navig service config
        navig service config --edit
    """
    import json

    from navig.daemon.entry import save_default_config

    config_path = save_default_config()

    if show:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        ch.info("Daemon Configuration")
        ch.console.print(f"  File: {config_path}")
        ch.console.print()
        for k, v in cfg.items():
            ch.console.print(f"  {k}: {v}")
    else:
        # Open in editor
        import subprocess

        editor = "code" if sys.platform == "win32" else (os.environ.get("EDITOR", "nano"))
        try:
            subprocess.run([editor, str(config_path)])
        except Exception:
            ch.info(f"Edit manually: {config_path}")
