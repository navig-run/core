#!/usr/bin/env python3
"""
NAVIG System Tray Application

A Windows system tray launcher for NAVIG agent and gateway services.
Inspired by Windows Hub — provides:
  - System tray icon with status indicator
  - Start/stop NAVIG gateway and agent
  - Quick access to common commands
  - Auto-start with Windows (optional)
  - Health monitoring with status updates

Usage:
    python navig/desktop/tray_app.py          # Launch tray app
    pythonw navig/desktop/tray_app.pyw        # Launch without console window
    navig tray start                      # Launch via CLI

Architecture:
    The tray app spawns NAVIG processes (gateway, agent) as subprocesses
    and monitors their health via periodic checks. No Windows Service
    registration required — runs as a regular user process.
"""

from __future__ import annotations

import io
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import typing
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from navig._daemon_defaults import _DAEMON_PORT
from navig.platform import paths

# Fix console encoding on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Logging setup — write to ~/.navig/logs/tray.log
# ---------------------------------------------------------------------------
LOG_DIR = paths.config_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "tray.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("navig-tray")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME = "NAVIG Tray"
REGISTRY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_VALUE = "NavigTray"
PYTHON_EXE = sys.executable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NAVIG_DIR = paths.config_dir()
SETTINGS_FILE = NAVIG_DIR / "tray_settings.json"
_PROC_GRACEFUL_TIMEOUT: int = 5  # Seconds to wait for a process to exit cleanly
HEALTH_INTERVAL = 15  # seconds


class Status(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"
    STARTING = "starting"


@dataclass
class TraySettings:
    """Persistent tray app settings."""

    auto_start: bool = False
    start_daemon_on_launch: bool = True
    start_gateway_on_launch: bool = False
    start_agent_on_launch: bool = False
    python_exe: str = ""
    gateway_port: int = _DAEMON_PORT

    def save(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        from navig.core.yaml_io import atomic_write_text

        atomic_write_text(SETTINGS_FILE, json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls) -> TraySettings:
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text())
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        return cls()


@dataclass
class ProcessState:
    """Track a managed subprocess."""

    name: str
    process: subprocess.Popen | None = None
    log_fh: typing.Any = None
    status: Status = Status.STOPPED
    started_at: datetime | None = None
    restart_count: int = 0
    last_error: str = ""

    @property
    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self):
        if self.process and self.is_alive:
            log.info("Stopping %s (PID %s)", self.name, self.process.pid)
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=_PROC_GRACEFUL_TIMEOUT)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            except Exception as e:
                log.error("Error stopping %s: %s", self.name, e)
        self.process = None
        if getattr(self, "log_fh", None) is not None:
            try:
                self.log_fh.close()
            except Exception:
                pass  # best-effort cleanup; file handle may already be closed
            self.log_fh = None
        self.status = Status.STOPPED
        self.started_at = None


# ---------------------------------------------------------------------------
# Tray Application
# ---------------------------------------------------------------------------
class NavigTray:
    """NAVIG system tray application."""

    def __init__(self):
        self.settings = TraySettings.load()
        self.gateway = ProcessState(name="Gateway")
        self.agent = ProcessState(name="Agent")
        self.daemon = ProcessState(name="Daemon")  # New: supervised daemon (bot+gateway+scheduler)
        self._icon = None
        self._health_thread: threading.Thread | None = None
        self._running = False
        self._python = self.settings.python_exe or PYTHON_EXE

        # Find the best icon
        self._icon_path = self._find_icon()

    def _find_icon(self) -> Path | None:
        """Find a NAVIG icon file."""
        candidates = [
            PROJECT_ROOT / "navig-icons" / "navig-main-256.png",
            PROJECT_ROOT / "navig-icons" / "navig-main-128.png",
            PROJECT_ROOT / "navig-icons" / "navig-main-512.png",
            PROJECT_ROOT / "navig-icons" / "navig-os-256.png",
            PROJECT_ROOT / "packages" / "navig-cloud" / "public" / "favicon.ico",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def _create_icon_image(self, status: Status = Status.STOPPED):
        """Create tray icon image with status color overlay."""
        from PIL import Image, ImageDraw

        size = 64

        if self._icon_path:
            try:
                img = Image.open(self._icon_path).resize((size, size), Image.LANCZOS)
                img = img.convert("RGBA")
            except Exception:
                img = Image.new("RGBA", (size, size), (40, 40, 40, 255))
        else:
            # Generate a simple N icon
            img = Image.new("RGBA", (size, size), (30, 30, 30, 255))
            draw = ImageDraw.Draw(img)
            draw.text((18, 12), "N", fill=(0, 200, 255, 255))

        # Draw status dot in bottom-right corner
        draw = ImageDraw.Draw(img)
        colors = {
            Status.STOPPED: (128, 128, 128, 255),
            Status.RUNNING: (0, 200, 80, 255),
            Status.ERROR: (220, 50, 50, 255),
            Status.STARTING: (255, 180, 0, 255),
        }
        color = colors.get(status, (128, 128, 128, 255))
        dot_r = 8
        cx, cy = size - dot_r - 2, size - dot_r - 2
        draw.ellipse(
            [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
            fill=color,
            outline=(0, 0, 0, 200),
            width=2,
        )
        return img

    # --- Process Management ---

    def _start_process(self, state: ProcessState, args: list[str]):
        """Start a NAVIG subprocess."""
        state.stop()
        state.status = Status.STARTING
        self._update_icon()

        cmd = [self._python, "-m", "navig"] + args
        log.info("Starting %s: %s", state.name, " ".join(cmd))

        try:
            log_file = LOG_DIR / f"{state.name.lower()}.log"
            fh = open(log_file, "a", encoding="utf-8")
            state.log_fh = fh
            state.process = subprocess.Popen(
                cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(Path.home()),
                creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
            )
            state.status = Status.RUNNING
            state.started_at = datetime.now()
            state.last_error = ""
            log.info("%s started (PID %s)", state.name, state.process.pid)
        except Exception as e:
            state.status = Status.ERROR
            state.last_error = str(e)
            log.error("Failed to start %s: %s", state.name, e)

        self._update_icon()

    def start_gateway(self):
        self._start_process(self.gateway, ["gateway", "start"])

    def stop_gateway(self):
        self.gateway.stop()
        self._update_icon()
        log.info("Gateway stopped")

    def start_agent(self):
        self._start_process(self.agent, ["agent", "start", "--foreground"])

    def stop_agent(self):
        self.agent.stop()
        self._update_icon()
        log.info("Agent stopped")

    # --- Daemon (Telegram Bot + Gateway + Scheduler) ---

    def _kill_orphan_bots(self):
        """Find and kill any orphan navig_bot.py processes."""
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*navig_bot.py*' } | Select-Object ProcessId | ForEach-Object { $_.ProcessId }",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            pids = [
                int(p.strip()) for p in result.stdout.strip().split("\n") if p.strip().isdigit()
            ]
            for pid in pids:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True,
                        timeout=_PROC_GRACEFUL_TIMEOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    log.info("Killed orphan bot process PID %s", pid)
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            if pids:
                log.info("Cleaned up %s orphan bot process(es)", len(pids))
        except Exception as e:
            log.error("Orphan bot cleanup failed: %s", e)

    def start_daemon(self):
        """Start the NAVIG daemon supervisor (manages bot, gateway, scheduler)."""
        # Stop existing daemon gracefully
        self._stop_daemon_graceful()
        # Kill any orphan bot processes from previous runs
        self._kill_orphan_bots()

        self.daemon.status = Status.STARTING
        self._update_icon()

        # Use pythonw.exe for completely invisible operation
        pythonw = Path(self._python).parent / "pythonw.exe"
        exe = str(pythonw) if pythonw.exists() else self._python
        cmd = [exe, "-m", "navig.daemon.entry"]
        log.info("Starting Daemon: %s", " ".join(cmd))

        try:
            log_file = LOG_DIR / "daemon-tray.log"
            fh = open(log_file, "a", encoding="utf-8")
            self.daemon.log_fh = fh
            self.daemon.process = subprocess.Popen(
                cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(Path.home()),
                creationflags=(
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                    if sys.platform == "win32"
                    else 0
                ),
            )
            self.daemon.status = Status.RUNNING
            self.daemon.started_at = datetime.now()
            self.daemon.last_error = ""
            log.info("Daemon started (PID %s)", self.daemon.process.pid)
        except Exception as e:
            self.daemon.status = Status.ERROR
            self.daemon.last_error = str(e)
            log.error("Failed to start Daemon: %s", e)

        self._update_icon()

    def _stop_daemon_graceful(self):
        """Stop the daemon with graceful shutdown so it cleans up children."""
        daemon_pid = None

        # Get PID from our tracked process or PID file
        if self.daemon.process and self.daemon.is_alive:
            daemon_pid = self.daemon.process.pid
        else:
            try:
                pid_file = paths.config_dir() / "daemon" / "supervisor.pid"
                if pid_file.exists():
                    daemon_pid = int(pid_file.read_text().strip())
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        if daemon_pid:
            log.info("Stopping daemon PID %s gracefully...", daemon_pid)
            try:
                # Send CTRL_BREAK_EVENT — caught by SIGBREAK handler in supervisor
                # This triggers graceful _shutdown() which kills children properly
                if sys.platform == "win32":
                    import ctypes

                    ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, daemon_pid)  # CTRL_BREAK = 1
                else:
                    os.kill(daemon_pid, signal.SIGTERM)
            except Exception as e:
                log.warning("Graceful signal failed: %s", e)

            # Wait up to 8s for graceful shutdown
            for _ in range(16):
                time.sleep(0.5)
                alive = False
                if self.daemon.process and self.daemon.is_alive or self._is_daemon_running():
                    alive = True
                if not alive:
                    break
            else:
                # Force kill if graceful failed
                log.warning("Force-killing daemon PID %s", daemon_pid)
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(daemon_pid), "/T"],
                        capture_output=True,
                        timeout=_PROC_GRACEFUL_TIMEOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        # Clean up tracked state
        self.daemon.process = None
        self.daemon.status = Status.STOPPED
        self.daemon.started_at = None

        # Clean up PID/state files
        for f in ["supervisor.pid", "state.json"]:
            try:
                p = paths.config_dir() / "daemon" / f
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    def stop_daemon(self):
        """Stop the NAVIG daemon and all its children."""
        self._stop_daemon_graceful()
        # Kill any orphan bots that survived
        self._kill_orphan_bots()
        self._update_icon()
        log.info("Daemon stopped")

    def _is_daemon_running(self) -> bool:
        """Check if daemon is running (locally spawned or externally)."""
        if self.daemon.is_alive:
            return True
        # Check PID file for externally started daemon
        try:
            daemon_pid_file = paths.config_dir() / "daemon" / "supervisor.pid"
            if daemon_pid_file.exists():
                pid = int(daemon_pid_file.read_text().strip())
                if sys.platform == "win32":
                    import ctypes

                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x100000, False, pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        return True
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        return False

    # --- Auto-start ---

    def _get_autostart_enabled(self) -> bool:
        """Check if auto-start is enabled in Windows Registry."""
        if sys.platform != "win32":
            return False
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, REGISTRY_VALUE)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _set_autostart(self, enabled: bool):
        """Toggle Windows auto-start via Registry."""
        if sys.platform != "win32":
            return
        try:
            import winreg

            # Use CreateKeyEx to ensure the key exists and we have write access
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE
            )
            if enabled:
                # Use pythonw.exe for silent launch
                pythonw = Path(self._python).parent / "pythonw.exe"
                if not pythonw.exists():
                    pythonw = Path(self._python)
                pyw_script = PROJECT_ROOT / "scripts" / "navig_tray.pyw"
                if not pyw_script.exists():
                    # Fallback to .py
                    pyw_script = PROJECT_ROOT / "scripts" / "navig_tray.py"
                value = f'"{pythonw}" "{pyw_script}"'
                winreg.SetValueEx(key, REGISTRY_VALUE, 0, winreg.REG_SZ, value)
                log.info("Auto-start enabled: %s", value)
            else:
                try:
                    winreg.DeleteValue(key, REGISTRY_VALUE)
                except FileNotFoundError:
                    pass  # file already gone; expected
                log.info("Auto-start disabled")
            winreg.CloseKey(key)

            self.settings.auto_start = enabled
            self.settings.save()
        except Exception as e:
            log.error("Failed to set auto-start: %s", e)

    # --- Health Monitor ---

    def _health_loop(self):
        """Background thread: monitor process health and update icon."""
        while self._running:
            changed = False

            for state in (self.gateway, self.agent, self.daemon):
                if state.status == Status.RUNNING and not state.is_alive:
                    exit_code = state.process.returncode if state.process else -1
                    state.status = Status.ERROR
                    state.last_error = f"Exited with code {exit_code}"
                    log.warning("%s died (exit %s)", state.name, exit_code)
                    changed = True

            # Check for externally-started daemon
            if self.daemon.status == Status.STOPPED and self._is_daemon_running():
                self.daemon.status = Status.RUNNING
                changed = True
            elif self.daemon.status == Status.RUNNING and not self._is_daemon_running():
                self.daemon.status = Status.STOPPED
                changed = True

            if changed:
                self._update_icon()

            time.sleep(HEALTH_INTERVAL)

    # --- Icon & Menu ---

    def _overall_status(self) -> Status:
        """Aggregate status across all services."""
        statuses = [self.gateway.status, self.agent.status, self.daemon.status]
        if Status.ERROR in statuses:
            return Status.ERROR
        if Status.STARTING in statuses:
            return Status.STARTING
        if Status.RUNNING in statuses:
            return Status.RUNNING
        return Status.STOPPED

    def _tooltip(self) -> str:
        dm = self.daemon.status.value
        gw = self.gateway.status.value
        ag = self.agent.status.value
        return f"NAVIG — Daemon: {dm} | Gateway: {gw} | Agent: {ag}"

    def _update_icon(self):
        """Refresh tray icon and tooltip."""
        if self._icon:
            try:
                self._icon.icon = self._create_icon_image(self._overall_status())
                self._icon.title = self._tooltip()
            except Exception as e:
                log.error("Icon update failed: %s", e)

    def _build_menu(self):
        """Build the tray context menu items.

        Returns a tuple of ``pystray.MenuItem`` — called fresh on every
        right-click because we pass this *method* (callable) to
        ``pystray.Menu(self._build_menu)`` in ``run()``.
        This means every open reflects the latest process states.
        """
        import pystray

        # --- helpers ---
        def _bg(fn):
            """Wrap action to run in a background thread."""

            def wrapper(icon, item):
                threading.Thread(target=fn, daemon=True).start()

            return wrapper

        def _toggle_autostart(icon, item):
            self._set_autostart(not self._get_autostart_enabled())

        dm_running = self._is_daemon_running()
        gw_running = self.gateway.status == Status.RUNNING
        ag_running = self.agent.status == Status.RUNNING

        # --- status label ---
        parts = []
        if dm_running:
            pid_str = f" (PID {self.daemon.process.pid})" if self.daemon.is_alive else ""
            parts.append(f"Bot: ON{pid_str}")
        if gw_running:
            parts.append("GW: ON")
        if ag_running:
            parts.append("Agent: ON")
        status_text = " | ".join(parts) if parts else "All services stopped"

        return (
            # ── Header ──
            pystray.MenuItem(f"NAVIG  —  {status_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            # ── Telegram Bot (daemon) ──
            pystray.MenuItem(
                "Start Telegram Bot" if not dm_running else "Restart Telegram Bot",
                _bg(self.start_daemon),
            ),
            pystray.MenuItem(
                "Stop Telegram Bot",
                _bg(self.stop_daemon),
                enabled=dm_running,
            ),
            pystray.Menu.SEPARATOR,
            # ── Hosts & Remote ──
            pystray.MenuItem(
                "Hosts",
                pystray.Menu(
                    pystray.MenuItem(
                        "Host Status",
                        _bg(lambda: self._run_navig_interactive(["host", "status"])),
                    ),
                    pystray.MenuItem(
                        "List Hosts",
                        _bg(lambda: self._run_navig_interactive(["host", "list"])),
                    ),
                    pystray.MenuItem(
                        "Test Connection",
                        _bg(lambda: self._run_navig_interactive(["host", "test"])),
                    ),
                ),
            ),
            # ── Database ──
            pystray.MenuItem(
                "Database",
                pystray.Menu(
                    pystray.MenuItem(
                        "List Databases",
                        _bg(lambda: self._run_navig_interactive(["db", "list"])),
                    ),
                    pystray.MenuItem(
                        "Show Tables",
                        _bg(lambda: self._run_navig_interactive(["db", "tables"])),
                    ),
                    pystray.MenuItem(
                        "DB Status",
                        _bg(lambda: self._run_navig_interactive(["db", "status"])),
                    ),
                ),
            ),
            # ── Vault / Credentials ──
            pystray.MenuItem(
                "Vault",
                pystray.Menu(
                    pystray.MenuItem(
                        "List Credentials",
                        _bg(lambda: self._run_navig_interactive(["cred", "list"])),
                    ),
                    pystray.MenuItem(
                        "Show Vault Info",
                        _bg(lambda: self._run_navig_interactive(["cred", "show"])),
                    ),
                ),
            ),
            # ── Skills ──
            pystray.MenuItem(
                "Skills",
                pystray.Menu(
                    pystray.MenuItem(
                        "List Skills",
                        _bg(lambda: self._run_navig_interactive(["skills", "list"])),
                    ),
                    pystray.MenuItem(
                        "Installed Skills",
                        _bg(lambda: self._run_navig_interactive(["skills", "installed"])),
                    ),
                ),
            ),
            # ── Backups ──
            pystray.MenuItem(
                "Backups",
                pystray.Menu(
                    pystray.MenuItem(
                        "Backup Status",
                        _bg(lambda: self._run_navig_interactive(["backup", "status"])),
                    ),
                    pystray.MenuItem(
                        "List Backups",
                        _bg(lambda: self._run_navig_interactive(["backup", "list"])),
                    ),
                    pystray.MenuItem(
                        "Run Backup Now",
                        _bg(lambda: self._run_navig_interactive(["backup", "run"])),
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            # ── Advanced Services ──
            pystray.MenuItem(
                "Advanced",
                pystray.Menu(
                    pystray.MenuItem(
                        "Start Gateway (standalone)",
                        _bg(self.start_gateway),
                        enabled=not gw_running,
                    ),
                    pystray.MenuItem(
                        "Stop Gateway",
                        _bg(self.stop_gateway),
                        enabled=gw_running,
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "Start Agent (standalone)",
                        _bg(self.start_agent),
                        enabled=not ag_running,
                    ),
                    pystray.MenuItem(
                        "Stop Agent",
                        _bg(self.stop_agent),
                        enabled=ag_running,
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "Service Status",
                        _bg(lambda: self._run_navig_interactive(["service", "status"])),
                    ),
                    pystray.MenuItem(
                        "View Daemon Logs",
                        _bg(
                            lambda: self._run_navig_interactive(
                                ["service", "logs", "--lines", "50"]
                            )
                        ),
                    ),
                    pystray.MenuItem("Open NAVIG Terminal", _bg(lambda: self._open_navig_shell())),
                ),
            ),
            # ── Settings ──
            pystray.MenuItem(
                "Settings",
                pystray.Menu(
                    pystray.MenuItem(
                        "Auto-start tray with Windows",
                        _toggle_autostart,
                        checked=lambda item: self._get_autostart_enabled(),
                    ),
                    pystray.MenuItem(
                        "Start bot when tray opens",
                        lambda icon, item: self._toggle_setting("start_daemon_on_launch"),
                        checked=lambda item: self.settings.start_daemon_on_launch,
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "Open Config Folder", _bg(lambda: os.startfile(str(NAVIG_DIR)))
                    ),
                    pystray.MenuItem("Open Log Folder", _bg(lambda: os.startfile(str(LOG_DIR)))),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Stop All & Exit",
                self._on_quit,
            ),
        )

    def _run_navig(self, args: list[str]):
        """Run a navig CLI command quietly (no visible window)."""
        cmd = [self._python, "-m", "navig"] + args
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
            )
        except Exception as e:
            log.error("Failed to run navig command: %s", e)

    def _run_navig_interactive(self, args: list[str]):
        """Run a navig CLI command in a visible terminal that stays open.

        Uses ``cmd /k`` so the console window remains after the command
        finishes — the user can read the output and close manually.
        """
        navig_cmd = f'"{self._python}" -m navig {" ".join(args)}'
        if sys.platform == "win32":
            # cmd /k keeps the window open; title shows the command
            title = f"NAVIG — {' '.join(args)}"
            full = f'cmd /k "title {title} && {navig_cmd}"'
            try:
                subprocess.Popen(full, creationflags=subprocess.CREATE_NEW_CONSOLE)
            except Exception as e:
                log.error("Failed to run interactive command: %s", e)
        else:
            cmd = [self._python, "-m", "navig"] + args
            try:
                subprocess.Popen(cmd)
            except Exception as e:
                log.error("Failed to run interactive command: %s", e)

    def _open_navig_shell(self):
        """Open an interactive terminal pre-loaded for navig commands."""
        if sys.platform == "win32":
            title = "NAVIG Terminal"
            # Set title, show version, then drop to prompt
            init = f'title {title} && "{self._python}" -m navig --version && echo. && echo Type "navig --help" for commands && echo.'
            try:
                subprocess.Popen(f'cmd /k "{init}"', creationflags=subprocess.CREATE_NEW_CONSOLE)
            except Exception as e:
                log.error("Failed to open NAVIG shell: %s", e)

    def _toggle_setting(self, attr: str):
        """Toggle a boolean TraySettings field and persist."""
        current = getattr(self.settings, attr, False)
        setattr(self.settings, attr, not current)
        self.settings.save()
        log.info("Setting %s = %s", attr, not current)

    def _on_quit(self, icon, item):
        """Stop everything and exit."""
        log.info("Shutting down NAVIG Tray")
        self._running = False
        self.stop_daemon()
        self.gateway.stop()
        self.agent.stop()
        icon.stop()

    # --- Main ---

    def run(self):
        """Start the tray application."""
        import pystray

        log.info("NAVIG Tray starting")

        self._running = True

        # Start health monitor
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()

        # Auto-start services if configured
        if self.settings.start_daemon_on_launch:
            self.start_daemon()
        if self.settings.start_gateway_on_launch:
            self.start_gateway()
        if self.settings.start_agent_on_launch:
            self.start_agent()

        # Create and run tray icon
        # Pass _build_menu as callable so pystray rebuilds the menu on every
        # right-click — this gives us live enabled/disabled/checked states.
        self._icon = pystray.Icon(
            name="navig",
            icon=self._create_icon_image(self._overall_status()),
            title=self._tooltip(),
            menu=pystray.Menu(self._build_menu),
        )

        log.info("Tray icon running")
        self._icon.run()  # Blocks until icon.stop()
        log.info("NAVIG Tray stopped")


def main():
    """Entry point."""
    # Single instance check via lock file
    lock_file = NAVIG_DIR / "tray.lock"
    NAVIG_DIR.mkdir(parents=True, exist_ok=True)

    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            # Check if PID is still alive
            if sys.platform == "win32":
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(
                    0x1000, False, pid
                )  # PROCESS_QUERY_LIMITED_INFORMATION
                if handle:
                    kernel32.CloseHandle(handle)
                    print(f"NAVIG Tray is already running (PID {pid})")
                    return
            else:
                os.kill(pid, 0)
                print(f"NAVIG Tray is already running (PID {pid})")
                return
        except (ValueError, OSError, ProcessLookupError):
            pass  # Stale lock, continue

    # Write lock
    lock_file.write_text(str(os.getpid()))

    try:
        app = NavigTray()
        app.run()
    finally:
        try:
            lock_file.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass  # Intentionally ignored  # best-effort; failure is non-critical


if __name__ == "__main__":
    main()
