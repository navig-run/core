"""
NAVIG Daemon Supervisor

A lightweight process supervisor that keeps NAVIG subsystems alive:
  - Telegram bot  (primary)
  - Gateway server (optional)
  - Scheduler/cron (optional)

Features:
  - Auto-restart crashed children with exponential back-off
  - PID file management
  - Structured log files with rotation
  - Graceful shutdown on SIGINT / SIGTERM / console close
  - Health-check endpoint (optional TCP port)
  - Designed to be wrapped by NSSM / Task Scheduler / WinSW
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from navig.core.yaml_io import atomic_write_text
from navig.platform import paths

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAVIG_HOME = paths.config_dir()
DAEMON_DIR = NAVIG_HOME / "daemon"
PID_FILE = DAEMON_DIR / "supervisor.pid"
STATE_FILE = DAEMON_DIR / "state.json"
_PROC_GRACEFUL_TIMEOUT: int = 5  # Seconds to wait for a process to exit cleanly


def _resolve_log_dir() -> Path:
    """Resolve log directory using navig.platform.paths (respects OS conventions)."""
    return paths.log_dir()


LOG_DIR = _resolve_log_dir()

MAX_RESTART_DELAY = 120  # seconds
INITIAL_RESTART_DELAY = 2
HEALTH_CHECK_INTERVAL = 30  # seconds


def _ensure_dirs() -> None:
    DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _make_logger(name: str, log_file: Path, level: int = logging.INFO) -> logging.Logger:
    """Create a rotating-file logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        # Also log to stderr when running in foreground
        if sys.stderr.isatty():
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(formatter)
            logger.addHandler(sh)
    # Keep daemon logs isolated from root handlers (e.g., Rich console).
    logger.propagate = False
    return logger


# ---------------------------------------------------------------------------
# Child process descriptor
# ---------------------------------------------------------------------------
class ChildProcess:
    """Describes and manages one supervised child process."""

    def __init__(
        self,
        name: str,
        command: list[str],
        *,
        env_extra: dict[str, str] | None = None,
        cwd: Path | None = None,
        enabled: bool = True,
        critical: bool = False,
    ):
        self.name = name
        self.command = command
        self.env_extra = env_extra or {}
        self.cwd = cwd
        self.enabled = enabled
        self.critical = critical  # supervisor exits if a critical child fails permanently

        self.process: subprocess.Popen | None = None
        self.restart_count = 0
        self.last_start: float | None = None
        self.last_exit_code: int | None = None
        self._backoff = INITIAL_RESTART_DELAY
        self._stopped = False  # True when intentionally stopped

    # -- lifecycle ----------------------------------------------------------

    def start(self, logger: logging.Logger) -> bool:
        """Launch the child. Returns True on success."""
        if self._stopped or not self.enabled:
            return False
        try:
            env = {**os.environ, **self.env_extra}

            # Redirect child output directly to a log file (avoids PIPE
            # buffering and the duplicate-line problem where a child
            # writes to both stdout and stderr).
            child_log = LOG_DIR / f"{self.name}.log"
            self._log_fh = open(child_log, "a", encoding="utf-8", errors="replace")

            kwargs: dict[str, Any] = {
                "env": env,
                "cwd": str(self.cwd) if self.cwd else None,
                "stdout": self._log_fh,
                "stderr": self._log_fh,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(self.command, **kwargs)
            self.last_start = time.monotonic()
            self.restart_count += 1
            logger.info(
                "Started %s (pid=%d, attempt=%d) -> %s",
                self.name,
                self.process.pid,
                self.restart_count,
                child_log,
            )
            return True
        except Exception as exc:
            logger.error("Failed to start %s: %s", self.name, exc)
            return False

    def stop(self, logger: logging.Logger, timeout: float = 10) -> None:
        """Gracefully stop the child."""
        self._stopped = True
        if self.process is None or self.process.poll() is not None:
            self._close_log()
            return
        pid = self.process.pid
        logger.info("Stopping %s (pid=%d)...", self.name, pid)
        try:
            if sys.platform == "win32":
                # Use taskkill /T to kill the process tree (catches any grandchildren)
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=_PROC_GRACEFUL_TIMEOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.process.wait(timeout=timeout)
            else:
                self.process.send_signal(signal.SIGTERM)
                self.process.wait(timeout=timeout)
            logger.info("Stopped %s cleanly", self.name)
        except subprocess.TimeoutExpired:
            logger.warning("Force-killing %s (pid=%d)", self.name, pid)
            self.process.kill()
            try:
                self.process.wait(timeout=_PROC_GRACEFUL_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        except Exception as exc:
            logger.error("Error stopping %s: %s", self.name, exc)
        self._close_log()

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def poll(self) -> int | None:
        """Check if child exited. Returns exit code or None."""
        if self.process is None:
            return None
        rc = self.process.poll()
        if rc is not None:
            self.last_exit_code = rc
        return rc

    def drain_output(self, logger: logging.Logger, child_logger: logging.Logger) -> None:
        """No-op - child output goes directly to log files now."""
        pass

    def _close_log(self) -> None:
        """Close the child log file handle if open."""
        fh = getattr(self, "_log_fh", None)
        if fh:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            self._log_fh = None

    @property
    def next_restart_delay(self) -> float:
        """Exponential back-off on restart delay."""
        delay = self._backoff
        self._backoff = min(self._backoff * 2, MAX_RESTART_DELAY)
        return delay

    def reset_backoff(self) -> None:
        self._backoff = INITIAL_RESTART_DELAY

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "pid": self.process.pid if self.process and self.is_alive() else None,
            "alive": self.is_alive(),
            "restart_count": self.restart_count,
            "last_exit_code": self.last_exit_code,
        }


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------
class NavigDaemon:
    """
    Main NAVIG supervisor daemon.

    Usage::

        daemon = NavigDaemon()
        daemon.add_telegram_bot()   # always
        daemon.add_gateway()        # optional
        daemon.run()                # blocks until shutdown
    """

    def __init__(self, *, health_port: int = 0):
        _ensure_dirs()
        self.logger = _make_logger("navig.daemon", LOG_DIR / "daemon.log")
        self.child_logger = _make_logger("navig.daemon.children", LOG_DIR / "children.log")
        self.children: list[ChildProcess] = []
        self._running = False
        self._health_port = health_port
        self._health_server: Any = None

    # -- child registration ------------------------------------------------

    def add_child(self, child: ChildProcess) -> None:
        self.children.append(child)

    def add_telegram_bot(
        self,
        *,
        bot_script: Path | None = None,
        python_exe: str | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> None:
        """Register the Telegram bot as a supervised child."""
        python = python_exe or sys.executable
        if bot_script is not None and bot_script.exists():
            self.add_child(
                ChildProcess(
                    name="telegram-bot",
                    command=[python, str(bot_script)],
                    cwd=bot_script.parent,
                    env_extra=env_extra or {},
                    critical=True,
                )
            )
            self.logger.info("Registered telegram-bot: %s", bot_script)
            return

        if bot_script is not None and not bot_script.exists():
            self.logger.warning(
                "Telegram bot script not found, skipping registration: %s", bot_script
            )
            return

        self.add_child(
            ChildProcess(
                name="telegram-bot",
                command=[python, "-m", "navig.daemon.telegram_worker"],
                env_extra=env_extra or {},
                critical=True,
            )
        )
        self.logger.info("Registered telegram-bot: module navig.daemon.telegram_worker")

    def add_gateway(
        self,
        *,
        python_exe: str | None = None,
        port: int = 8789,
    ) -> None:
        """Register the gateway server as a supervised child."""
        python = python_exe or sys.executable
        self.add_child(
            ChildProcess(
                name="gateway",
                command=[
                    python,
                    "-m",
                    "navig",
                    "gateway",
                    "start",
                    "--port",
                    str(port),
                ],
                env_extra={},
            )
        )
        self.logger.info("Registered gateway (port %d)", port)

    def add_scheduler(self, *, python_exe: str | None = None) -> None:
        """Register the cron scheduler."""
        python = python_exe or sys.executable
        self.add_child(
            ChildProcess(
                name="scheduler",
                command=[python, "-m", "navig.scheduler.cron_service"],
                env_extra={},
            )
        )

    # -- PID management ----------------------------------------------------

    def _write_pid(self) -> None:
        atomic_write_text(PID_FILE, str(os.getpid()))

    def _remove_pid(self) -> None:
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)

    @staticmethod
    def read_pid() -> int | None:
        if PID_FILE.exists():
            try:
                return int(PID_FILE.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                return None
        return None

    @staticmethod
    def is_running() -> bool:
        """Check if a daemon is already running."""
        pid = NavigDaemon.read_pid()
        if pid is None:
            return False
        try:
            if sys.platform == "win32":
                import ctypes
                import ctypes.wintypes

                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not handle:
                    # Process does not exist — clean up stale PID file
                    PID_FILE.unlink(missing_ok=True)
                    return False
                # Verify the process hasn't exited (STILL_ACTIVE = 259 = 0x103)
                exit_code = ctypes.wintypes.DWORD()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if exit_code.value != 259:  # process has exited
                    kernel32.CloseHandle(handle)
                    PID_FILE.unlink(missing_ok=True)
                    return False
                # Verify the PID belongs to a Python process (not a reused PID)
                # QueryFullProcessImageNameW is fast — no subprocess needed
                buf = ctypes.create_unicode_buffer(1024)
                buf_size = ctypes.wintypes.DWORD(1024)
                kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_size))
                kernel32.CloseHandle(handle)
                exe_name = Path(buf.value).name.lower() if buf.value else ""
                if exe_name not in ("python.exe", "pythonw.exe", "python3.exe"):
                    # PID reused by a non-Python process — stale PID file
                    PID_FILE.unlink(missing_ok=True)
                    return False
                return True
            else:
                os.kill(pid, 0)
                return True
        except (OSError, ProcessLookupError):
            PID_FILE.unlink(missing_ok=True)
            return False

    @staticmethod
    def _verify_daemon_pid(pid: int) -> bool:
        """Check if the PID actually belongs to a navig daemon process."""
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    [
                        "powershell",
                        "-Command",
                        f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=_PROC_GRACEFUL_TIMEOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                cmdline = result.stdout.strip()
                return "navig" in cmdline.lower() and "daemon" in cmdline.lower()
            else:
                cmdline_path = Path(f"/proc/{pid}/cmdline")
                if cmdline_path.exists():
                    cmdline = cmdline_path.read_text()
                    return "navig" in cmdline and "daemon" in cmdline
                return False
        except Exception:
            return False

    # -- state persistence -------------------------------------------------

    def _write_state(self) -> None:
        """Write daemon state to JSON for external queries."""
        state = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "children": [c.to_dict() for c in self.children],
        }
        try:
            atomic_write_text(STATE_FILE, json.dumps(state, indent=2))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    @staticmethod
    def read_state() -> dict[str, Any] | None:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except Exception:
                return None
        return None

    # -- health-check TCP server -------------------------------------------

    async def _start_health_server(self) -> None:
        if self._health_port <= 0:
            return

        async def handler(reader, writer):
            state = {
                "status": "ok",
                "pid": os.getpid(),
                "children": [c.to_dict() for c in self.children],
            }
            body = json.dumps(state)
            response = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"\r\n{body}"
            )
            writer.write(response.encode())
            await writer.drain()
            writer.close()

        self._health_server = await asyncio.start_server(handler, "127.0.0.1", self._health_port)
        self.logger.info("Health-check listening on 127.0.0.1:%d", self._health_port)

    # -- main loop ---------------------------------------------------------

    def run(self) -> None:
        """Blocking entry-point - runs the supervisor until shutdown."""
        if self.is_running():
            pid = self.read_pid()
            # Double-check: verify the PID is actually a navig daemon, not a stale PID
            if self._verify_daemon_pid(pid):
                self.logger.error(
                    "Daemon already running (pid=%s). Use 'navig service stop' first.",
                    pid,
                )
                print(
                    f"ERROR: Daemon already running (pid={pid}). Stop it first with: navig service stop"
                )
                return
            else:
                self.logger.warning("Stale PID file (pid=%s) - removing and starting fresh", pid)
                self._remove_pid()

        # Sweep stale daemon generations from previous restarts before
        # writing the new PID file so their log handles are released.
        swept = self._kill_orphan_daemons(exclude_pid=os.getpid())
        if swept:
            self.logger.info("Swept %d orphan daemon PID(s): %s", len(swept), swept)

        self._running = True
        self._write_pid()
        self.logger.info("=== NAVIG Daemon starting (pid=%d) ===", os.getpid())

        # Register signal handlers
        if sys.platform == "win32":
            signal.signal(signal.SIGBREAK, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        try:
            asyncio.run(self._supervisor_loop())
        except KeyboardInterrupt:
            pass  # user interrupted; clean exit
        finally:
            self._shutdown()

    def _signal_handler(self, signum, frame):
        self.logger.info("Received signal %s - shutting down", signum)
        self._running = False

    async def _supervisor_loop(self) -> None:
        """Core supervision loop."""
        await self._start_health_server()

        try:
            # Initial start of all enabled children
            for child in self.children:
                if child.enabled:
                    child.start(self.logger)
            self._write_state()

            while self._running:
                for child in self.children:
                    if not child.enabled or child._stopped:
                        continue

                    # Drain output
                    child.drain_output(self.logger, self.child_logger)

                    # Check if dead
                    rc = child.poll()
                    if rc is not None:
                        # If process ran for > 60s, reset back-off (healthy run)
                        if child.last_start and (time.monotonic() - child.last_start) > 60:
                            child.reset_backoff()

                        delay = child.next_restart_delay
                        self.logger.warning(
                            "%s exited with code %d - restarting in %.0fs",
                            child.name,
                            rc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        if not self._running:
                            break

                        child.start(self.logger)
                        self._write_state()

                await asyncio.sleep(2)  # poll interval
        finally:
            if self._health_server is not None:
                self._health_server.close()
                await self._health_server.wait_closed()
                self._health_server = None

    def _shutdown(self) -> None:
        """Stop all children and clean up."""
        self.logger.info("Shutting down all children...")
        for child in self.children:
            child.stop(self.logger)
        self._remove_pid()
        if STATE_FILE.exists():
            STATE_FILE.unlink(missing_ok=True)
        self.logger.info("=== NAVIG Daemon stopped ===")

    # -- external control --------------------------------------------------

    @staticmethod
    def _kill_orphan_daemons(exclude_pid: int | None = None) -> list[int]:
        """Kill all pythonw/python processes that are navig daemon instances.

        Finds every python process whose command line contains 'navig.daemon'
        (or 'navig\\daemon\\entry') except *exclude_pid* and the current process,
        then force-kills them with taskkill /F on Windows or SIGKILL on POSIX.

        Returns the list of PIDs that were targeted.
        """
        current_pid = os.getpid()
        killed: list[int] = []

        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            "Get-CimInstance Win32_Process"
                            " | Where-Object {"
                            "  $_.CommandLine -and ("
                            "    $_.CommandLine -like '*navig.daemon*' -or"
                            "    $_.CommandLine -like '*navig\\\\daemon\\\\entry*'"
                            "  )"
                            " }"
                            " | Select-Object -ExpandProperty ProcessId"
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for token in result.stdout.split():
                    try:
                        found_pid = int(token.strip())
                    except ValueError:
                        continue
                    if found_pid == current_pid:
                        continue
                    if exclude_pid is not None and found_pid == exclude_pid:
                        continue
                    killed.append(found_pid)
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(found_pid), "/T"],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
            except Exception:  # noqa: BLE001
                pass  # best-effort; never crash the stop path
        else:
            try:
                import signal as _signal
                result = subprocess.run(
                    ["pgrep", "-f", "navig.daemon"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for token in result.stdout.split():
                    try:
                        found_pid = int(token.strip())
                    except ValueError:
                        continue
                    if found_pid == current_pid:
                        continue
                    if exclude_pid is not None and found_pid == exclude_pid:
                        continue
                    killed.append(found_pid)
                    try:
                        os.kill(found_pid, _signal.SIGKILL)
                    except OSError:
                        pass
            except Exception:  # noqa: BLE001
                pass  # best-effort

        return killed

    @staticmethod
    def stop_running_daemon() -> bool:
        """Send stop signal to a running daemon. Returns True if stopped."""
        pid = NavigDaemon.read_pid()
        if pid is None:
            # No PID file, but there may still be orphan daemon processes —
            # sweep them up before reporting not-running.
            NavigDaemon._kill_orphan_daemons()
            return False
        try:
            if sys.platform == "win32":
                # Use taskkill with /T (tree) for clean shutdown
                r = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T"],
                    capture_output=True,
                )
                # taskkill writes to stdout on Windows, not stderr
                tk_out = (r.stdout + r.stderr).lower()
                if r.returncode != 0 and b"not found" in tk_out:
                    # Process already gone — clean up stale PID file
                    PID_FILE.unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
            else:
                os.kill(pid, signal.SIGTERM)
            # Wait a moment for clean exit
            for _ in range(20):
                time.sleep(0.5)
                if not NavigDaemon.is_running():
                    PID_FILE.unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
            # Force kill
            if sys.platform == "win32":
                r = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/T"],
                    capture_output=True,
                )
                tk_out = (r.stdout + r.stderr).lower()
                if r.returncode != 0 and b"not found" in tk_out:
                    PID_FILE.unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
                if r.returncode == 0:
                    # Force-kill succeeded — process is gone
                    time.sleep(0.5)
                    PID_FILE.unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
            else:
                force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(pid, force_signal)
            # Wait briefly after force-kill and only then report success.
            for _ in range(10):
                time.sleep(0.2)
                if not NavigDaemon.is_running():
                    PID_FILE.unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
            NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
            return False
        except ProcessLookupError:
            # Process already gone
            if PID_FILE.exists():
                PID_FILE.unlink(missing_ok=True)
            NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
            return True
        except PermissionError:
            return False
        except OSError:
            if not NavigDaemon.is_running():
                if PID_FILE.exists():
                    PID_FILE.unlink(missing_ok=True)
                NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                return True
            return False

