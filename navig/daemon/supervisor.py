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

from navig._daemon_defaults import _GATEWAY_PORT
from navig.core.proc_text import console_encoding
from navig.core.yaml_io import atomic_write_text
from navig.platform import paths

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PROC_GRACEFUL_TIMEOUT: int = 5  # Seconds to wait for a process to exit cleanly

# Test seams — when ``None`` (the normal state), the resolvers below evaluate
# ``paths.config_dir()`` at CALL time so NAVIG_CONFIG_DIR isolation set after
# import still applies. Frozen module constants meant a test could read (or
# unlink!) the operator's REAL supervisor.pid / state.json
# (see navig/vault/migrate.py:_legacy_db_path).
PID_FILE: Path | None = None
STATE_FILE: Path | None = None


def _under_pytest() -> bool:
    """True when this process is a test run rather than the operator's daemon."""
    return "pytest" in sys.modules


def _pytest_state_dir() -> Path:
    """A throwaway, per-process daemon-state dir used ONLY under pytest.

    Why this exists: ``paths.config_dir()`` falls back to the operator's REAL
    ``~/.navig`` whenever a test has not isolated it, so a bare ``NavigDaemon()``
    resolved ``_pid_file()`` to the operator's live ``supervisor.pid``. Measured on
    the operator's own machine (2026-09-04): their real ``daemon.log`` recorded
    ``Stale PID file (pid=7) - removing and starting fresh`` -- pid 7 is a value that
    only exists inside a test stub -- and three seconds later
    ``Swept 1 orphan daemon PID(s): [56800]``, where 56800 was their LIVE supervisor.

    That is the whole failure: a test overwrites the pid file, the live daemon is no
    longer *recorded* as running, and the next start therefore classifies it as an
    orphan of a previous generation and ``taskkill /F /T``s it. Same config dir, so
    the (correct) config-dir scoping in :meth:`_kill_orphan_daemons` cannot help --
    the daemon really is "ours", it was just erased from the file that vouches for it.
    The operator loses their bot, with no traceback and no shutdown line.

    Per-process (``os.getpid()``) so xdist workers cannot fight over one path.
    """
    import tempfile

    return Path(tempfile.gettempdir()) / f"navig-pytest-daemon-{os.getpid()}"


def _daemon_dir() -> Path:
    # An explicitly isolated brain (NAVIG_CONFIG_DIR) is honoured by config_dir()
    # itself. The pytest branch covers the tests that isolate NOTHING -- see
    # _pytest_state_dir for what that cost the operator.
    if _under_pytest() and not os.environ.get("NAVIG_CONFIG_DIR"):
        return _pytest_state_dir() / "daemon"
    return paths.config_dir() / "daemon"


def _pid_file() -> Path:
    return PID_FILE if PID_FILE is not None else _daemon_dir() / "supervisor.pid"


def _state_file() -> Path:
    return STATE_FILE if STATE_FILE is not None else _daemon_dir() / "state.json"


def _capture_code_identity() -> dict[str, Any]:
    """Snapshot which code THIS process loaded at boot, so a later ``navig doctor`` can
    tell whether the running daemon is executing STALE code — i.e. the source moved on
    disk after boot (a merge, a ``git pull``, a branch switch) while the daemon kept the
    old modules in memory ("merged but not live"). Best-effort; never raises.

    Records the source dir + git HEAD/branch for an editable checkout, and always the
    package version. Git is detected via ``git rev-parse`` (which walks UP to the repo
    root) rather than ``(<src>/.git).exists()`` — in this monorepo ``.git`` lives at the
    repo root, not inside the editable ``core/`` src dir.
    """
    info: dict[str, Any] = {"captured_at": datetime.now(timezone.utc).isoformat()}
    try:
        import navig as _nav  # noqa: PLC0415

        info["version"] = str(getattr(_nav, "__version__", "") or "")
    except Exception:  # noqa: BLE001
        pass  # version is a bonus; the git commit below is the primary signal
    try:
        # <src>/navig/daemon/supervisor.py -> parents[2] == <src> (the editable root)
        src_dir = Path(__file__).resolve().parents[2]
        info["src"] = str(src_dir)
        rev = subprocess.run(
            ["git", "-C", str(src_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5, encoding="utf-8", errors="replace",
        )
        if rev.returncode == 0 and rev.stdout.strip():
            info["install"] = "git"
            info["commit"] = rev.stdout.strip()
            br = subprocess.run(
                ["git", "-C", str(src_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5, encoding="utf-8", errors="replace",
            )
            if br.returncode == 0 and br.stdout.strip():
                info["branch"] = br.stdout.strip()
        else:
            info["install"] = "pip"
    except Exception:  # noqa: BLE001
        pass  # git absent / timed out — version-only identity is still useful
    return info


def _elevation_hint(pid: int) -> str:
    """The actionable message for an 'access denied' stop — almost always an
    elevation mismatch (the daemon runs elevated; this terminal does not)."""
    if os.name == "nt":
        return (
            f"it's running elevated (Administrator) but this terminal is not "
            f"(pid {pid}), so Windows denied the stop. Re-run in an Administrator "
            f"terminal, or force it:  taskkill /F /PID {pid} /T"
        )
    return (
        f"permission denied signalling pid {pid} — it may run as a different user or "
        f"elevated. Try:  sudo kill -9 {pid}"
    )


def _resolve_log_dir() -> Path:
    """Resolve the daemon's log directory.

    Normally ``paths.log_dir()`` -- the OS-idiomatic location that ``navig service
    logs`` and ``navig doctor`` read. TWO cases must NOT resolve there, because both
    mean "this process is not the operator's daemon":

    * **Under pytest.** ``NavigDaemon.__init__`` opens ``daemon.log`` before any test
      body runs, so a test cannot opt out by isolating late. The operator's real
      ``daemon.log`` carried lines naming pytest tmp dirs
      (``.../pytest-of-subdose/popen-gw3/test_add_telegram_bot0/...``) interleaved
      with genuine boot records -- the one file you would read to find out why the
      daemon died, filled with noise by the test suite.

    ``NAVIG_CONFIG_DIR`` is deliberately NOT a trigger here, and that is a CORRECTION.
    It looked like the right signal for "an isolated brain", but the Windows scheduled
    task sets it on every launch (``_task_bootstrap_args`` bakes
    ``NAVIG_CONFIG_DIR=<home>`` in), so keying on it moved the REAL daemon's logs.
    Measured within minutes of shipping it: ``~/.navig/logs/daemon.log`` was live at
    17:40 while ``%LOCALAPPDATA%/navig/logs/daemon.log`` -- the file ``navig service
    logs`` and the deck viewer actually read -- sat frozen at 17:35. That is the same
    split-brain the surrounding work exists to remove.

    The pytest check alone is sufficient for the isolation it was added for:
    ``"pytest" in sys.modules`` is true for the whole test process, so it applies
    however late a test sets its env.

    An explicit ``NAVIG_LOG_DIR`` always wins: it is a deliberate statement of intent
    and ``paths.log_dir()`` already honours it.
    """
    if os.environ.get("NAVIG_LOG_DIR"):
        return paths.log_dir()
    if _under_pytest():
        return _pytest_state_dir() / "logs"
    return paths.log_dir()

MAX_RESTART_DELAY = 120  # seconds
INITIAL_RESTART_DELAY = 2
HEALTH_CHECK_INTERVAL = 30  # seconds


def _ensure_dirs() -> None:
    _daemon_dir().mkdir(parents=True, exist_ok=True)
    _resolve_log_dir().mkdir(parents=True, exist_ok=True)


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
            child_log = _resolve_log_dir() / f"{self.name}.log"
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
            # The log handle was opened before Popen — close it so a crash-looping child
            # that fails to spawn doesn't leak one file descriptor per restart attempt.
            self._close_log()
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

    # Human-readable reason set by stop_running_daemon() when it returns False, so a
    # CLI caller can tell the operator WHY (e.g. an elevation mismatch) — a bare False
    # is unactionable. Reset at the start of every stop attempt.
    _last_stop_error: str | None = None

    def __init__(self, *, health_port: int = 0):
        _ensure_dirs()
        self.logger = _make_logger("navig.daemon", _resolve_log_dir() / "daemon.log")
        self.child_logger = _make_logger("navig.daemon.children", _resolve_log_dir() / "children.log")
        self.children: list[ChildProcess] = []
        self._running = False
        self._health_port = health_port
        self._health_server: Any = None
        # Snapshot the code this process loaded at boot (git commit / version) so
        # `navig doctor` can flag a daemon that's running stale code after the source
        # moved on disk. Captured ONCE here — never recomputed — so it reflects boot,
        # not whatever HEAD happens to be at the next _write_state().
        self._boot_code: dict[str, Any] = _capture_code_identity()

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
        port: int = _GATEWAY_PORT,
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
        atomic_write_text(_pid_file(), str(os.getpid()))

    def _remove_pid(self) -> None:
        if _pid_file().exists():
            _pid_file().unlink(missing_ok=True)

    @staticmethod
    def read_pid() -> int | None:
        """The daemon's PID — but only if it is STILL the process that wrote the file.

        A pidfile records a NUMBER, and a number is not an identity. This returned the
        recorded integer whenever the file parsed, so after a crash or a reboot — when the
        OS has handed that number to something else — every consumer acted on a stranger:

          * ``stop_running_daemon`` sent ``taskkill /PID n /T`` and then ``/F /T`` to it
            with no identity check at all, killing an unrelated process TREE;
          * ``is_running`` reported the daemon up because *something* answered to the
            number, so ``navig service status`` / ``doctor`` / the tray all lied;
          * ``navig service start`` refused to start ("already running").

        ``pid_from_pidfile`` is the canonical answer and was already adopted by
        ``navig agent stop``, ``gateway``, ``tray`` and the MCP agent tool — the daemon
        supervisor was the holdout, which is the "harden one path into a destructive
        action, enumerate EVERY path" failure. It compares the process ``create_time``
        against the file's mtime: the owner writes the pidfile just after starting, so a
        process that recycled the number necessarily started after the file was written.

        ``None`` now means missing / unparseable / dead / unreadable / recycled — all of
        which mean "not running", the safe answer. Every caller already handles ``None``,
        and ``navig service stop``'s fast path turns it into a clean "Daemon is not
        running" instead of a force-kill aimed at whatever inherited the number.

        No ``cmdline_contains`` marker: the daemon legitimately runs under several shapes
        (``pythonw -m navig``, a console launch, a frozen exe), and a marker that failed to
        match would make a LIVE daemon unstoppable — a worse failure than the one fixed.
        """
        from navig.daemon.single_instance import pid_from_pidfile  # noqa: PLC0415

        return pid_from_pidfile(_pid_file())

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
                    _pid_file().unlink(missing_ok=True)
                    return False
                # Verify the process hasn't exited (STILL_ACTIVE = 259 = 0x103)
                exit_code = ctypes.wintypes.DWORD()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if exit_code.value != 259:  # process has exited
                    kernel32.CloseHandle(handle)
                    _pid_file().unlink(missing_ok=True)
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
                    _pid_file().unlink(missing_ok=True)
                    return False
                return True
            else:
                os.kill(pid, 0)
                return True
        except (OSError, ProcessLookupError):
            _pid_file().unlink(missing_ok=True)
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
                    encoding=console_encoding(),
                    errors="replace",
                    timeout=_PROC_GRACEFUL_TIMEOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                cmdline = result.stdout.strip()
                return "navig" in cmdline.lower() and "daemon" in cmdline.lower()
            else:
                cmdline_path = Path(f"/proc/{pid}/cmdline")
                if cmdline_path.exists():
                    cmdline = cmdline_path.read_text(encoding="utf-8")
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
            "boot_code": getattr(self, "_boot_code", {}),
        }
        try:
            atomic_write_text(_state_file(), json.dumps(state, indent=2))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    @staticmethod
    def read_state() -> dict[str, Any] | None:
        if _state_file().exists():
            try:
                return json.loads(_state_file().read_text(encoding="utf-8"))
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

        try:
            self._health_server = await asyncio.start_server(
                handler, "127.0.0.1", self._health_port
            )
        except OSError as exc:
            # The health endpoint is DIAGNOSTICS; the supervisor's job is running children.
            # This call sits at the top of `_supervisor_loop` OUTSIDE its try/except, so an
            # escaping bind error meant not one child was ever started — the auxiliary
            # killing the essential. `--health-port` is set when installing a PERSISTENT
            # service, so the service manager would have restarted the daemon into the same
            # failure indefinitely.
            #
            # Deliberately NOT falling back to another port: monitoring is pointed at the
            # port the operator chose, so quietly moving it would answer on a port nobody
            # watches — worse than being honestly absent.
            self.logger.error(
                "Health-check port %d unavailable (%s) — daemon continuing WITHOUT it. "
                "Something else holds the port, or on Windows it is inside a reserved range "
                "(check: netsh interface ipv4 show excludedportrange protocol=tcp). "
                "Children are unaffected; choose another with --health-port.",
                self._health_port,
                exc,
            )
            self._health_server = None
            return
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
                # Avoid print() when sys.stdout is None (pythonw.exe / windowless mode)
                if sys.stdout is not None:
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
        if _state_file().exists():
            _state_file().unlink(missing_ok=True)
        self.logger.info("=== NAVIG Daemon stopped ===")

    # -- external control --------------------------------------------------

    @staticmethod
    def _enumerate_navig_pids() -> list[int]:
        """Return every PID whose command line looks like a navig daemon/gateway/worker.

        Machine-wide and config-dir-AGNOSTIC by design — the caller
        (:meth:`_kill_orphan_daemons`) scopes which of these are actually ours before
        killing anything. Best-effort: a failed enumeration returns ``[]``.
        """
        pids: list[int] = []
        try:
            if sys.platform == "win32":
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
                            "    $_.CommandLine -like '*navig\\\\daemon\\\\entry*' -or"
                            "    $_.CommandLine -like '*navig gateway start*' -or"
                            "    $_.CommandLine -like '*telegram_worker*'"
                            "  )"
                            " }"
                            " | Select-Object -ExpandProperty ProcessId"
                        ),
                    ],
                    capture_output=True,
                    encoding=console_encoding(),
                    errors="replace",
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                result = subprocess.run(
                    ["pgrep", "-f", r"navig\.daemon|navig gateway start|telegram_worker"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
        except Exception:  # noqa: BLE001 — best-effort; never crash the stop path
            return pids
        for token in result.stdout.split():
            try:
                pids.append(int(token.strip()))
            except ValueError:
                continue
        return pids

    @staticmethod
    def _force_kill_pid(pid: int) -> None:
        """Force-kill a PID + its tree — taskkill /F /T on Windows, SIGKILL on POSIX."""
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    @staticmethod
    def _kill_orphan_daemons(
        exclude_pid: int | None = None,
        *,
        config_dir: Path | None = None,
        config_dir_reader=None,
        pids: list[int] | None = None,
        killer=None,
        keep: set[int] | None = None,
    ) -> list[int]:
        """Force-kill stale navig daemon/gateway/worker processes **for OUR brain only**.

        Sweeps stale daemon generations left by previous restarts. The enumeration
        (PowerShell / pgrep) is machine-wide, so every candidate PID is scoped by its
        effective ``NAVIG_CONFIG_DIR`` before being killed: a process whose config dir
        differs from ours — OR cannot be read — is LEFT ALONE. This mirrors the gateway
        supersede guard (``single_instance.kill_other_instances`` / ``config_dir_of``);
        without it a ``navig service stop`` under a different ``NAVIG_CONFIG_DIR`` would
        force-kill the operator's live brain (all lights green, no shutdown line — the
        documented catastrophe). "Refusing to kill" is the safe failure: a stale
        SAME-brain instance is still reaped, and a port bind self-heals.

        **Ancestors are protected.** Excluding only ``current_pid`` is not enough: the
        enumeration matches any command line *mentioning* ``navig.daemon``, which
        includes this process's own launcher chain (``py.exe`` → ``python.exe``), and
        the kill is ``taskkill /F /T`` — a TREE kill. Sweeping the launcher therefore
        killed the daemon that was starting, before it ever wrote its pid file: the
        Task Scheduler entry from ``navig service install`` ran, exited 1, and left no
        log, so autostart looked installed and delivered nothing. ``ancestor_pids()``
        is the module that exists to answer "the tree we must never kill"; the gateway
        supersede guard already used it and this sweeper was the holdout.

        Returns the list of PIDs that were killed. ``config_dir`` / ``config_dir_reader``
        / ``pids`` / ``killer`` / ``keep`` are injectable for testing.
        """
        current_pid = os.getpid()
        if keep is None:
            try:
                from navig.daemon.single_instance import ancestor_pids

                keep = ancestor_pids()
            except Exception:  # noqa: BLE001 — self-exclusion below still holds
                keep = {current_pid}
        try:
            from navig.platform import paths

            mine = (config_dir if config_dir is not None else paths.config_dir()).resolve()
        except Exception:  # noqa: BLE001 — can't identify our own brain → kill nothing (safe)
            return []

        if config_dir_reader is not None:
            read_cfg = config_dir_reader
        else:
            from navig.daemon.single_instance import config_dir_of

            read_cfg = config_dir_of
        kill = killer if killer is not None else NavigDaemon._force_kill_pid
        candidates = pids if pids is not None else NavigDaemon._enumerate_navig_pids()

        killed: list[int] = []
        for found_pid in candidates:
            if found_pid == current_pid or found_pid in keep:
                continue
            if exclude_pid is not None and found_pid == exclude_pid:
                continue
            # Config-dir scoping — NEVER kill a process that isn't ours or can't be
            # identified. A different config dir is a different brain; an unreadable
            # one might be. Refusing to kill costs at most a surviving stale instance.
            # Normalize BOTH sides: a reader may hand back an unresolved path, and a
            # near-miss would silently widen the sweep back to machine-wide — the exact
            # bug this scoping exists to prevent.
            try:
                theirs = read_cfg(found_pid)
                theirs = Path(theirs).resolve() if theirs is not None else None
            except Exception:  # noqa: BLE001
                theirs = None
            if theirs is None or theirs != mine:
                continue
            killed.append(found_pid)
            try:
                kill(found_pid)
            except Exception:  # noqa: BLE001
                pass  # best-effort; never crash the stop path

        return killed

    @staticmethod
    def stop_running_daemon() -> bool:
        """Send stop signal to a running daemon. Returns True if stopped.

        On failure sets :attr:`_last_stop_error` with an actionable reason (an
        elevation mismatch is the common one) so the CLI can tell the operator WHY
        and how to recover — a bare False leaves them stuck."""
        NavigDaemon._last_stop_error = None
        pid = NavigDaemon.read_pid()
        if pid is None:
            # No PID file, but there may still be orphan daemon processes —
            # sweep them up before reporting not-running.
            NavigDaemon._kill_orphan_daemons()
            return False
        try:
            if sys.platform == "win32":
                # Graceful first: taskkill /T (tree) without /F. Windows-only.
                r = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                # taskkill writes its message to stdout on Windows, not stderr.
                out = (r.stdout + r.stderr).decode("utf-8", "replace")
                low = out.lower()
                if r.returncode != 0:
                    if "not found" in low:
                        # Process already gone — clean up stale PID file.
                        _pid_file().unlink(missing_ok=True)
                        NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                        return True
                    if "access is denied" in low or "access denied" in low:
                        # Force-kill will be denied too (an elevation mismatch), so
                        # don't burn the 10 s graceful wait — report and bail now.
                        NavigDaemon._last_stop_error = _elevation_hint(pid)
                        return False
                # else: graceful sent, or "can only be terminated forcefully" — fall
                # through to the wait + force-kill below.
            else:
                os.kill(pid, signal.SIGTERM)
            # Wait a moment for clean exit
            for _ in range(20):
                time.sleep(0.5)
                if not NavigDaemon.is_running():
                    _pid_file().unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
            # Force kill
            if sys.platform == "win32":
                r = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/T"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                out = (r.stdout + r.stderr).decode("utf-8", "replace")
                low = out.lower()
                if r.returncode == 0:
                    # Force-kill succeeded — process is gone
                    time.sleep(0.5)
                    _pid_file().unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
                if "not found" in low:
                    _pid_file().unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
                # Force-kill genuinely failed — record WHY so the caller can act.
                if "access is denied" in low or "access denied" in low:
                    NavigDaemon._last_stop_error = _elevation_hint(pid)
                else:
                    NavigDaemon._last_stop_error = (
                        f"taskkill couldn't stop pid {pid}: {out.strip() or 'unknown error'}"
                    )
            else:
                force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(pid, force_signal)
            # Wait briefly after force-kill and only then report success.
            for _ in range(10):
                time.sleep(0.2)
                if not NavigDaemon.is_running():
                    _pid_file().unlink(missing_ok=True)
                    NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                    return True
            NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
            if NavigDaemon._last_stop_error is None:
                NavigDaemon._last_stop_error = f"pid {pid} is still running after a force-kill."
            return False
        except ProcessLookupError:
            # Process already gone
            if _pid_file().exists():
                _pid_file().unlink(missing_ok=True)
            NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
            return True
        except PermissionError:
            NavigDaemon._last_stop_error = _elevation_hint(pid)
            return False
        except OSError as exc:
            if not NavigDaemon.is_running():
                if _pid_file().exists():
                    _pid_file().unlink(missing_ok=True)
                NavigDaemon._kill_orphan_daemons(exclude_pid=pid)
                return True
            NavigDaemon._last_stop_error = f"couldn't stop pid {pid}: {exc}"
            return False

