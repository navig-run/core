"""
NAVIG Tray CLI Commands

Commands for managing the NAVIG system tray launcher on Windows.
The tray app provides a system tray icon for starting/stopping
NAVIG gateway and agent services without keeping a terminal open.
"""

import os
import subprocess
import sys
from pathlib import Path

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

tray_app = typer.Typer(
    name="tray",
    help="Windows system tray launcher for NAVIG services",
    no_args_is_help=True,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRAY_SCRIPT = PROJECT_ROOT / "scripts" / "navig_tray.py"
TRAY_PYW = PROJECT_ROOT / "scripts" / "navig_tray.pyw"
LOCK_FILE = Path.home() / ".navig" / "tray.lock"
INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "install-tray.ps1"


def _is_tray_running() -> tuple[bool, int | None]:
    """Check if tray app is already running."""
    if not LOCK_FILE.exists():
        return False, None
    try:
        pid = int(LOCK_FILE.read_text().strip())
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True, pid
        else:
            os.kill(pid, 0)
            return True, pid
    except (ValueError, OSError, ProcessLookupError):
        return False, None
    return False, None


@tray_app.command("start")
def tray_start(
    foreground: bool = typer.Option(
        False,
        "--foreground",
        "-f",
        help="Run in foreground (default: background/silent)",
    ),
):
    """
    Launch the NAVIG system tray app.

    Starts a system tray icon that lets you control NAVIG services
    (gateway, agent) from the Windows taskbar. Right-click the icon
    for the menu.

    Examples:
        navig tray start              # Launch in background (silent)
        navig tray start --foreground # Launch with console output
    """
    running, pid = _is_tray_running()
    if running:
        ch.warning(f"NAVIG Tray is already running (PID {pid})")
        return

    if not TRAY_SCRIPT.exists():
        ch.error(f"Tray script not found: {TRAY_SCRIPT}")
        raise typer.Exit(1)

    # Check dependencies
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as _exc:
        ch.error("Missing dependencies: pystray, Pillow")
        ch.info("Install with: pip install pystray Pillow")
        raise typer.Exit(1) from _exc

    python = sys.executable

    if foreground:
        ch.info("Starting NAVIG Tray (foreground)...")
        try:
            subprocess.run([python, str(TRAY_SCRIPT)], check=True)
        except KeyboardInterrupt:
            ch.info("Tray stopped")
    else:
        # Launch silently using pythonw.exe if available
        pythonw = Path(python).parent / "pythonw.exe"
        launcher = str(pythonw) if pythonw.exists() else python
        script = str(TRAY_PYW) if TRAY_PYW.exists() else str(TRAY_SCRIPT)

        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

        subprocess.Popen(
            [launcher, script],
            creationflags=flags,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ch.success("NAVIG Tray launched in background")
        ch.info("Right-click the tray icon (near clock) for the menu")


@tray_app.command("stop")
def tray_stop():
    """
    Stop the NAVIG tray app.

    Terminates the running tray process gracefully.

    Example:
        navig tray stop
    """
    running, pid = _is_tray_running()
    if not running:
        ch.warning("NAVIG Tray is not running")
        return

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=True
            )
        else:
            os.kill(pid, 15)  # SIGTERM

        # Clean up lock file
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        ch.success(f"NAVIG Tray stopped (PID {pid})")
    except Exception as e:
        ch.error(f"Failed to stop tray: {e}")
        raise typer.Exit(1) from e


@tray_app.command("status")
def tray_status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Check if NAVIG tray app is running.

    Example:
        navig tray status
        navig tray status --json
    """
    running, pid = _is_tray_running()

    if json_output:
        import json as json_mod

        ch.console.print(json_mod.dumps({"running": running, "pid": pid}))
        return

    if running:
        ch.success(f"NAVIG Tray is running (PID {pid})")
    else:
        ch.info("NAVIG Tray is not running")
        ch.info("Start with: navig tray start")


@tray_app.command("install")
def tray_install(
    auto_start: bool = typer.Option(
        False, "--auto-start", "-a", help="Enable auto-start with Windows"
    ),
):
    """
    Install NAVIG Tray (desktop shortcut + optional auto-start).

    Creates a desktop shortcut and optionally registers NAVIG Tray
    to start automatically when you log into Windows.

    Examples:
        navig tray install
        navig tray install --auto-start
    """
    if sys.platform != "win32":
        ch.error("Tray install is only supported on Windows")
        raise typer.Exit(1)

    if not INSTALL_SCRIPT.exists():
        ch.error(f"Install script not found: {INSTALL_SCRIPT}")
        raise typer.Exit(1)

    args = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(INSTALL_SCRIPT)]
    if auto_start:
        args.append("-AutoStart")
    args.extend(["-Python", sys.executable])

    try:
        subprocess.run(args, check=True)
    except subprocess.CalledProcessError as e:
        ch.error(f"Installation failed: {e}")
        raise typer.Exit(1) from e


@tray_app.command("uninstall")
def tray_uninstall():
    """
    Remove NAVIG Tray auto-start and desktop shortcut.

    Example:
        navig tray uninstall
    """
    if sys.platform != "win32":
        ch.error("Tray uninstall is only supported on Windows")
        raise typer.Exit(1)

    # Stop if running
    running, pid = _is_tray_running()
    if running:
        tray_stop()

    # Remove auto-start from registry
    try:
        import winreg

        key = winreg.OpenSubKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_WRITE,
        )
        if key:
            try:
                winreg.DeleteValue(key, "NavigTray")
                ch.success("Auto-start removed from registry")
            except FileNotFoundError:
                ch.info("Auto-start was not configured")
            winreg.CloseKey(key)
    except Exception as e:
        ch.warning(f"Could not modify registry: {e}")

    # Remove desktop shortcut
    shortcut = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "NAVIG Tray.lnk"
    if shortcut.exists():
        shortcut.unlink()
        ch.success("Desktop shortcut removed")

    # Remove settings file
    settings_file = Path.home() / ".navig" / "tray_settings.json"
    if settings_file.exists():
        settings_file.unlink()
        ch.info("Settings file removed")

    ch.success("NAVIG Tray uninstalled")
