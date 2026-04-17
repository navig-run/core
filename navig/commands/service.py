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
import textwrap

import typer

from navig.lazy_loader import lazy_import
from navig.platform.paths import config_dir
from navig.platform.paths import log_dir as _log_dir

ch = lazy_import("navig.console_helper")


def _spawn_stop_watchdog(duration: int = 30) -> None:
    """Launch a detached process that kills new daemon spawns for *duration* seconds.

    The watchdog is written to a temporary ``.py`` file in the daemon directory
    (``~/.navig/daemon/navig_wdog_XXXX.py``) so that its command line is
    ``pythonw.exe /path/to/navig_wdog_XXXX.py`` — it does NOT contain the
    string ``navig.daemon``.  Earlier versions used ``python -c "...navig.daemon..."``
    which caused the watchdog to match its own ``*navig.daemon*`` kill-pattern
    inside :meth:`NavigDaemon._kill_orphan_daemons` and immediately self-destruct
    on the first 0.2 s tick.

    The watchdog reads the deadline timestamp written by :func:`set_watchdog_deadline`
    and loops, killing orphans every 0.2 s, until the deadline expires or the file
    is deleted (which :func:`service_start` does when the user deliberately restarts).
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    navig_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from navig.daemon.service_manager import DAEMON_DIR  # noqa: PLC0415

    deadline_file_str = str(DAEMON_DIR / "stop_watchdog_deadline")
    script = textwrap.dedent(f"""\
        # navig stop-watchdog: kills orphan daemon processes until deadline.
        # Launched as:  pythonw.exe <this_file>  -- no 'navig.daemon' in cmdline.
        import sys, time, os
        from pathlib import Path
        sys.path.insert(0, {navig_root!r})
        deadline_file = Path({deadline_file_str!r})
        try:
            deadline = float(deadline_file.read_text(encoding='utf-8').strip())
        except Exception:
            sys.exit(0)
        try:
            from navig.daemon.supervisor import NavigDaemon
        except Exception:
            sys.exit(0)
        while time.time() < deadline:
            if not deadline_file.exists():
                break
            NavigDaemon._kill_orphan_daemons()
            time.sleep(0.2)
        try:
            os.unlink(__file__)
        except Exception:
            pass
    """)

    try:
        DAEMON_DIR.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix="navig_wdog_",
            dir=str(DAEMON_DIR),
            delete=False,
            encoding="utf-8",
        )
        tmp.write(script)
        tmp.flush()
        tmp.close()
        watchdog_path = tmp.name
    except Exception:  # noqa: BLE001
        return  # can't write temp file — skip watchdog silently

    flags = 0
    if os.name == "nt":
        flags = (  # type: ignore[attr-defined]
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    try:
        subprocess.Popen(
            [sys.executable, watchdog_path],
            close_fds=True,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        pass  # watchdog is best-effort; never block the stop path


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

    from navig.core.yaml_io import atomic_write_text
    from navig.daemon import service_manager as sm
    from navig.daemon.entry import DEFAULT_DAEMON_CONFIG, save_default_config

    config_path = save_default_config()
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = DEFAULT_DAEMON_CONFIG.copy()
    except (json.JSONDecodeError, OSError):
        cfg = DEFAULT_DAEMON_CONFIG.copy()
    cfg["telegram_bot"] = bot
    cfg["gateway"] = gateway
    cfg["scheduler"] = scheduler
    cfg["health_port"] = health_port
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    atomic_write_text(tmp_path, json.dumps(cfg, indent=2))
    os.replace(tmp_path, config_path)

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
        import time

        # Check the stop-intent flag.
        # • If the flag is set and this is NOT an interactive terminal (e.g. a tray app
        #   or startup script calling `navig service start` automatically), refuse to
        #   start — honouring the deliberate user stop.
        # • If the flag is set and the caller IS an interactive terminal (a human
        #   explicitly typing `navig service start`), clear both the stop flag and the
        #   watchdog deadline so the daemon is allowed to start immediately.
        from navig.daemon.service_manager import (
            _pythonw_exe,
            clear_stop_flag,
            clear_watchdog_deadline,
            stop_flag_is_set,
            task_scheduler_enable,
        )

        if stop_flag_is_set():
            if not sys.stdin.isatty():
                ch.warning(
                    "Daemon was stopped by user intent — auto-restart blocked. "
                    "To restart interactively run:  navig service start"
                )
                return
            # Interactive user override: lift the stop lock and the watchdog window.
            clear_watchdog_deadline()
            clear_stop_flag()
        else:
            clear_stop_flag()

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

        time.sleep(2)
        if NavigDaemon.is_running():
            # Re-enable the Task Scheduler task (may have been disabled by
            # a prior 'navig service stop') so logon/failure-restart fires again.
            if os.name == "nt":
                task_scheduler_enable()  # silent if task not installed
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
    Also sweeps any orphan daemon generations left from prior restarts
    so that all log-file handles are released.

    Examples:
        navig service stop
    """
    import time

    from navig.daemon.service_manager import (
        set_stop_flag,
        set_watchdog_deadline,
        task_scheduler_disable,
    )
    from navig.daemon.supervisor import NavigDaemon

    # Step 0 — write stop guards BEFORE killing anything so that any external
    # watcher (tray app, Task Scheduler RestartOnFailure, HKCU\Run script) that
    # races to restart the daemon hits the entry.py guards.
    set_stop_flag()
    set_watchdog_deadline(30)

    # Step 1 — disable Task Scheduler task so RestartOnFailure cannot fire.
    if os.name == "nt":
        task_scheduler_disable()

    # Step 2 — stop the supervisor gracefully (SIGTERM → wait 10 s → force-kill).
    # stop_running_daemon() handles the supervisor PID; on exit it sweeps orphan
    # workers with exclude_pid set to the supervisor so workers are caught too.
    # We do NOT call task_scheduler_end() first: schtasks /end sends TerminateProcess
    # which bypasses the supervisor's _shutdown() and leaves workers orphaned;
    # stop_running_daemon() uses taskkill which triggers the graceful exit path.
    pid = NavigDaemon.read_pid()
    if pid is not None:
        ch.info(f"Stopping daemon (pid={pid})...")
        if not NavigDaemon.stop_running_daemon():
            if os.name == "nt":
                ch.error("Failed to stop daemon. Try: taskkill /F /PID " + str(pid))
            else:
                ch.error("Failed to stop daemon. Try: kill -9 " + str(pid))
            raise typer.Exit(1)

    # Step 3 — single cleanup sweep: trap any workers that outlived shutdown or
    # any orphan generations from prior incomplete stops.  The supervisor is now
    # confirmed dead so nothing can respawn these; one pass is sufficient.
    time.sleep(0.5)  # brief settle so any graceful child exits are complete
    swept = NavigDaemon._kill_orphan_daemons()
    if swept:
        ch.info(f"Swept {len(swept)} orphan daemon process(es): {swept}")
    elif pid is None:
        ch.info("Daemon is not running")

    # Step 4 — start the 30-second orphan-kill watchdog so any external restarter
    # that ignores entry.py guards (e.g. directly spawning pythonw.exe) is still
    # swept within 0.2 s of appearing.
    _spawn_stop_watchdog()

    if pid is not None:
        ch.success("Daemon stopped")


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

    from navig.daemon.service_manager import (
        _pythonw_exe,
        task_scheduler_disable,
        task_scheduler_enable,
    )
    from navig.daemon.supervisor import NavigDaemon

    # Disable the Task Scheduler task *before* killing so RestartOnFailure
    # cannot respawn the daemon while we are mid-restart (Windows only).
    if os.name == "nt":
        task_scheduler_disable()  # silent if task not installed

    if NavigDaemon.is_running():
        ch.info("Stopping daemon...")
        if not NavigDaemon.stop_running_daemon():
            ch.error("Failed to stop existing daemon")
            raise typer.Exit(1)
        time.sleep(1)
    else:
        # Sweep any orphan generations even if PID file says not running
        swept = NavigDaemon._kill_orphan_daemons()
        if swept:
            ch.info(f"Swept {len(swept)} orphan daemon process(es): {swept}")
            time.sleep(1)

    ch.info("Starting daemon...")
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
        # Re-enable the Task Scheduler task so logon/failure-restart works again.
        if os.name == "nt":
            task_scheduler_enable()  # silent if task not installed
        ch.success(f"Daemon restarted (pid={NavigDaemon.read_pid()})")
    else:
        ch.error("Daemon failed to start. Check: navig service logs")
        raise typer.Exit(1)


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
        running, detail = sm.status()
        state = NavigDaemon.read_state()
        out = {
            "running": running,
            "pid": NavigDaemon.read_pid(),
            "children": state.get("children", []) if state else [],
            "detail": detail,
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
        if not NavigDaemon.stop_running_daemon():
            ch.error("Failed to stop running daemon before uninstall")
            raise typer.Exit(1)

    ok, msg = sm.uninstall(method=method)
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
    lines: int = typer.Option(50, "--lines", "-n", min=1, help="Number of lines to show"),
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
    log_dir = _log_dir()

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
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            ch.error(f"Failed to read daemon config: {exc}")
            raise typer.Exit(1) from exc
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
