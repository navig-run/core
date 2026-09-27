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
from datetime import datetime, timedelta, timezone
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


def _resolved_log_file() -> str | None:
    """The path `navig.core.logging` attaches its file handler to — mirrored
    exactly (`ConfigManager.base_dir / "navig.log"`), never re-derived, so this
    cannot disagree with where the lines actually go. ``None`` when it cannot
    be resolved: an unknown is reported as unknown, not as a guess."""
    try:
        from navig.config import get_config_manager

        return str(get_config_manager().base_dir / "navig.log")
    except Exception:  # noqa: BLE001
        return None


def _state_file() -> Path:
    return STATE_FILE if STATE_FILE is not None else _daemon_dir() / "state.json"


def _own_create_time() -> float | None:
    """This process's start time (psutil), or None without psutil."""
    try:
        import psutil  # type: ignore[import-untyped]

        return float(psutil.Process().create_time())
    except Exception:  # noqa: BLE001
        return None


def _created_before(pid: int, instant: float) -> bool:
    """Was *pid* created strictly before *instant*? Unknown → True (the old behaviour:
    a candidate we cannot date is treated as a stale generation, as it always was)."""
    try:
        import psutil  # type: ignore[import-untyped]

        return float(psutil.Process(pid).create_time()) < instant
    except Exception:  # noqa: BLE001
        return True


#: How often the supervisor loop touches ``state.json`` while it is alive. The
#: file's mtime is the daemon's "last seen alive" — the fact a death incident
#: needs to DATE the death (not the detection, which the task can delay by five
#: minutes) and the fact `doctor` needs to tell a supervisor that is wedged from
#: one that is supervising. A touch is an ``os.utime``, no rewrite: 30 s is
#: cheap, and one interval of slack is the resolution a death gets dated to.
HEARTBEAT_S: float = 30.0
#: A heartbeat older than this, on a supervisor whose PROCESS is alive, means its
#: loop is stuck — three missed beats, so one slow tick under load stays quiet.
HEARTBEAT_STALE_S: float = 3 * HEARTBEAT_S


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
      (``.../pytest-of-<user>/popen-gw3/test_add_telegram_bot0/...``) interleaved
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
        # When THIS process started — set once in run(). `_write_state` used to
        # stamp `datetime.now()` on every call, and it is called on every child
        # restart, so the dashboard's "since HH:MM" moved each time the bot
        # child bounced.
        self._started_at: str | None = None
        self._last_beat: float = 0.0
        self._orphan_reported = False

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
    def _reap_stale_pid_file(pid: int, reason: str) -> None:
        """Delete a pid file that points at a dead process — and record why.

        A pid file whose process is gone is the fingerprint of an UNGRACEFUL death:
        a clean stop removes the file. This used to be reaped silently, in four
        places, so a daemon that was force-killed and later relaunched by the
        scheduled task left no trace at all — the operator's bot was deaf for
        minutes and nothing said so. The incident carries the dead pid and when
        that daemon started (the pid file's mtime), and reaches the operator
        through the same `config_incidents` push the config layer's rescues use.

        Recorded exactly once per death: the file is gone after this, so the next
        caller finds no pid file and records nothing.
        """
        pf = _pid_file()
        # Re-read before touching anything. Between the caller's read and now a
        # RELAUNCHING daemon may have written its own pid here (the task fires
        # on a 5-minute trigger; a `doctor` can race it). Reaping then deletes a
        # LIVE daemon's pid file: it keeps running, `is_running()` says no, and
        # the next start boots a second one. Only the number judged dead is ours.
        # A file already gone was reaped by another caller — record nothing, or
        # two concurrent status checks report one death twice.
        try:
            current = int(pf.read_text(encoding="utf-8").strip() or 0)
        except OSError:
            return
        except ValueError:
            current = pid
        if current != pid:
            return
        started_at: str | None = None
        try:
            started_at = datetime.fromtimestamp(pf.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            pass
        # The state file outlives an ungraceful death too (a clean stop removes
        # both), and its mtime is the last heartbeat: the death happened AFTER
        # it, within one interval. That dates the death — detection can be five
        # minutes later — and it closes the neighbour window, so a command run
        # against an already-dead daemon is not listed as a suspect.
        last_alive = NavigDaemon.last_seen_alive()
        pf.unlink(missing_ok=True)
        _state_file().unlink(missing_ok=True)
        try:
            from navig.core import incidents

            incidents.record(
                incidents.DAEMON_DIED_UNGRACEFULLY,
                previous_pid=pid,
                started_at=started_at,
                last_seen_alive=last_alive,
                reason=reason,
                nearby_commands=NavigDaemon._commands_before_now(started_at, last_seen_alive=last_alive),
            )
        except Exception:  # noqa: BLE001 — an observation must never break a status check
            pass

    @staticmethod
    def _commands_before_now(
        started_at: str | None,
        *,
        last_seen_alive: str | None = None,
        window_s: float = 15 * 60,
    ) -> list[dict]:
        """navig commands audited in the window before this detection — verbs only.

        The death is detected some time after it happened (the task relaunches
        within five minutes; a status check may be later still), so the window
        runs back from NOW, capped at the dead daemon's own start: anything
        before it started cannot have killed it. Tonight the answer would have
        been "navig cdp stop (session 9f39fd34) at 18:01:13Z" — the correlation
        that took an hour by hand.

        When the last heartbeat is known the window CLOSES there plus one
        interval (the death happened before the beat that never came): a
        command run after that ran against a daemon that was already dead and
        is not a suspect — five minutes of detection lag used to list it as one.

        ⚠ VERB ONLY. `details.command` holds the full line, and
        `navig config set gateway.auth.token <secret>` is an ordinary command —
        copying it here would put the secret into the incident log AND the
        Telegram push. Keep ``navig <group> <verb>``; drop everything after.
        """
        try:
            from navig.store.audit import get_audit_store

            end = datetime.now(timezone.utc)
            if last_seen_alive:
                try:
                    beat = datetime.fromisoformat(last_seen_alive)
                    if beat.tzinfo is None:
                        beat = beat.replace(tzinfo=timezone.utc)
                    # + one interval: the death is somewhere between the last
                    # beat and the one that never came; + 10 s of loop jitter.
                    end = min(end, beat + timedelta(seconds=HEARTBEAT_S + 10))
                except ValueError:
                    pass
            start = end - timedelta(seconds=window_s)
            if started_at:
                try:
                    born = datetime.fromisoformat(started_at)
                    if born.tzinfo is None:
                        born = born.replace(tzinfo=timezone.utc)
                    start = max(start, born)
                except ValueError:
                    pass
            if start >= end:
                return []
            fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
            rows = get_audit_store().events_between(start.strftime(fmt), end.strftime(fmt), limit=12)
        except Exception:  # noqa: BLE001 — the incident must still be recorded without this
            return []
        out: list[dict] = []
        for r in rows:
            details = r.get("details")
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except ValueError:
                    details = {}
            cmd = str((details or {}).get("command") or r.get("action") or "")
            verb = " ".join(cmd.split()[:3])  # "navig cdp stop" — never the arguments
            out.append(
                {
                    "at": str(r.get("timestamp") or ""),
                    "command": verb,
                    "session": str(r.get("session_id") or "")[:8] or None,
                    "status": r.get("status"),
                }
            )
        return out

    @staticmethod
    def owned_process_trees() -> list[dict[str, Any]]:
        """Every navig-owned long-lived process tree on this machine — the pids an
        external process sweeper must spare.

        A sweeper cannot tell the daemon from a leaked helper: both are python with
        the parent gone. The operator's hourly cleanup killed the daemon twice on
        2026-09-14 for exactly that reason. This is the contract: each entry is a
        pid FILE navig writes, resolved to its owner only if the process is older
        than the file naming it (``pid_from_pidfile`` — a recycled pid is not the
        owner), then expanded to every descendant. Roots:

        * ``daemon/supervisor.pid`` — the service supervisor; its children are the
          gateway and the telegram worker.
        * ``gateway.pid`` — a standalone ``navig gateway start`` (inside the
          supervisor it is already a descendant and is not listed twice).
        * ``agent/agent.pid`` — a standalone ``navig agent start``.
        * ``worker.pid`` — a standalone telegram worker (``navig bot start --background``
          with no service installed); written by the worker itself.

        Never raises; a root that is missing, dead or recycled contributes nothing.
        """
        from navig.daemon.single_instance import pid_from_pidfile

        roots = [
            ("supervisor", _pid_file()),
            ("gateway", paths.config_dir() / "gateway.pid"),
            ("agent", paths.config_dir() / "agent" / "agent.pid"),
            ("worker", paths.config_dir() / "worker.pid"),
        ]
        try:
            import psutil  # type: ignore[import-untyped]
        except ImportError:
            psutil = None  # type: ignore[assignment]
        seen: set[int] = set()
        out: list[dict[str, Any]] = []
        for role, pf in roots:
            try:
                root = pid_from_pidfile(pf)
            except Exception:  # noqa: BLE001
                root = None
            if root is None or root in seen:
                continue
            members: list[dict[str, Any]] = []
            if psutil is not None:
                try:
                    proc = psutil.Process(root)
                    for p in [proc, *proc.children(recursive=True)]:
                        try:
                            members.append({"pid": p.pid, "name": p.name()})
                        except Exception:  # noqa: BLE001
                            members.append({"pid": p.pid, "name": None})
                except Exception:  # noqa: BLE001
                    members = [{"pid": root, "name": None}]
            else:
                members = [{"pid": root, "name": None}]
            seen.update(m["pid"] for m in members)
            out.append({"role": role, "pid_file": str(pf), "root": root, "members": members})
        return out

    @staticmethod
    def parent_of(pid: int) -> dict[str, Any]:
        """Who launched the daemon — the process SHAPE an orphan sweep kills on.

        Returns ``{"ppid", "name", "alive", "shape"}`` where ``shape`` is one of:

        * ``"service"`` — the parent is the Task Scheduler / service host
          (``svchost.exe``, ``nssm.exe``) or init/systemd (pid 1). The daemon has a
          living parent for life. This is the shape a task-launched daemon has.
        * ``"orphan"`` — the parent is GONE. A daemon spawned detached from a CLI
          that then exited. On 2026-09-14 the operator's hourly process sweep
          killed exactly this shape twice (`KILL pythonw.exe 118488 ppid=62344
          gone`) — an external sweeper cannot tell it from a leaked helper.
        * ``"process"`` — a live, ordinary parent (a foreground `service start -f`,
          a dev shell): fine while that parent lives.
        * ``"unknown"`` — could not be read (no psutil, access denied).

        psutil's ``parent()`` pre-empts pid reuse (a parent that started AFTER
        the child is not its parent), so a recycled ppid reads as gone, not alive.
        """
        out: dict[str, Any] = {"ppid": None, "name": None, "alive": None, "shape": "unknown"}
        try:
            import psutil  # type: ignore[import-untyped]

            proc = psutil.Process(int(pid))
            out["ppid"] = proc.ppid()
            parent = proc.parent()
        except Exception:  # noqa: BLE001 — NoSuchProcess / AccessDenied / no psutil
            return out
        if parent is None:
            out["alive"] = False
            out["shape"] = "orphan" if out["ppid"] else "unknown"
            return out
        out["alive"] = True
        try:
            out["name"] = parent.name()
        except Exception:  # noqa: BLE001
            out["name"] = None
        name = (out["name"] or "").lower()
        if parent.pid == 1 or name in ("svchost.exe", "nssm.exe", "systemd", "init", "launchd"):
            out["shape"] = "service"
        else:
            out["shape"] = "process"
        return out

    @staticmethod
    def is_running() -> bool:
        """Check if a daemon is already running."""
        pid = NavigDaemon.read_pid()
        if pid is None:
            # `read_pid` answers None for missing AND for "the file names a process
            # that is dead or recycled" — and leaves the file where it is. Only the
            # second is a death. Tell them apart by the file, and record it: a pid
            # file that outlived its process is the one trace an ungraceful exit
            # leaves, and it used to be discarded here without a word.
            pf = _pid_file()
            if pf.exists():
                try:
                    stale = int(pf.read_text(encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    stale = 0
                NavigDaemon._reap_stale_pid_file(stale, "pid file names no live daemon")
            return False
        try:
            if sys.platform == "win32":
                import ctypes
                import ctypes.wintypes

                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not handle:
                    # Process does not exist — the previous daemon died without a stop
                    NavigDaemon._reap_stale_pid_file(pid, "process gone")
                    return False
                # Verify the process hasn't exited (STILL_ACTIVE = 259 = 0x103)
                exit_code = ctypes.wintypes.DWORD()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if exit_code.value != 259:  # process has exited
                    kernel32.CloseHandle(handle)
                    NavigDaemon._reap_stale_pid_file(pid, "process exited")
                    return False
                # Verify the PID belongs to a Python process (not a reused PID)
                # QueryFullProcessImageNameW is fast — no subprocess needed
                buf = ctypes.create_unicode_buffer(1024)
                buf_size = ctypes.wintypes.DWORD(1024)
                kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_size))
                kernel32.CloseHandle(handle)
                exe_name = Path(buf.value).name.lower() if buf.value else ""
                if exe_name not in ("python.exe", "pythonw.exe", "python3.exe"):
                    # PID reused by a non-Python process — the daemon died and its
                    # number was handed to something else; the file outlived it
                    NavigDaemon._reap_stale_pid_file(pid, f"pid reused by {exe_name or 'unknown'}")
                    return False
                return True
            else:
                os.kill(pid, 0)
                return True
        except (OSError, ProcessLookupError):
            NavigDaemon._reap_stale_pid_file(pid, "process lookup failed")
            return False

    @staticmethod
    def _verify_daemon_pid(pid: int) -> bool:
        """Check if the PID actually belongs to a navig daemon process.

        The caller has already identity-checked the pid (``is_running``). This is the
        second opinion from the command line, so "could not read it" must answer
        True: answering False deletes the live daemon's pidfile and boots a SECOND
        supervisor. That is exactly what happened on macOS, which has no ``/proc`` —
        the POSIX branch read ``/proc/<pid>/cmdline``, found nothing, and returned
        False for every live daemon.
        """
        cmdline = NavigDaemon._read_cmdline(pid)
        if cmdline is None:
            return True  # unreadable — trust the identity check that got us here
        low = cmdline.lower()
        return "navig" in low and "daemon" in low

    @staticmethod
    def _read_cmdline(pid: int) -> str | None:
        """The process's command line, or None when it cannot be read on this OS."""
        try:
            import psutil  # type: ignore[import-untyped]

            try:
                return " ".join(psutil.Process(pid).cmdline())
            except psutil.NoSuchProcess:
                return ""  # gone: definitely not our daemon
            except (psutil.Error, OSError):
                pass  # AccessDenied etc. — fall through to the OS tools
        except ImportError:
            pass
        return NavigDaemon._read_cmdline_os(pid)

    @staticmethod
    def _read_cmdline_os(pid: int) -> str | None:
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
                return result.stdout.strip() if result.returncode == 0 else None
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():  # Linux
                return cmdline_path.read_text(encoding="utf-8").replace("\0", " ")
            # macOS / BSD: no /proc — ps knows the full command line.
            result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PROC_GRACEFUL_TIMEOUT,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return None

    # -- state persistence -------------------------------------------------

    def _write_state(self) -> None:
        """Write daemon state to JSON for external queries."""
        state = {
            "pid": os.getpid(),
            "started_at": self._started_at or datetime.now(timezone.utc).isoformat(),
            "children": [c.to_dict() for c in self.children],
            "boot_code": getattr(self, "_boot_code", {}),
            # The file's mtime is the heartbeat; this says how often to expect
            # one, so a reader never hardcodes the interval — and its ABSENCE
            # tells `doctor` the running daemon predates heartbeats, which is
            # "unknown", not "wedged".
            "heartbeat_s": HEARTBEAT_S,
            # Where this daemon's navig.log actually lands. The logging setup
            # resolves it as `ConfigManager.base_dir / "navig.log"`, and
            # base_dir follows the cwd into a PROJECT .navig/ when the restart
            # was run from inside one — so `~/.navig/navig.log` simply stops
            # advancing, with nothing anywhere to say where the lines went.
            # Recorded here so `navig doctor` can name the file that is live.
            "log_file": _resolved_log_file(),
            "cwd": os.getcwd(),
        }
        try:
            atomic_write_text(_state_file(), json.dumps(state, indent=2))
            self._last_beat = time.monotonic()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    def _heartbeat(self) -> None:
        """Touch ``state.json`` every :data:`HEARTBEAT_S` — proof the LOOP is alive.

        Every other liveness check asks "is the pid alive?", which a supervisor
        stuck in a blocking call answers yes to while restarting nothing. The
        mtime is the one signal that comes from the loop itself. An ``os.utime``
        rewrites nothing; a state file that went missing is rewritten instead.
        """
        now = time.monotonic()
        if now - self._last_beat < HEARTBEAT_S:
            return
        self._last_beat = now
        try:
            sf = _state_file()
            if sf.exists():
                os.utime(sf, None)
            else:
                self._write_state()
        except OSError:
            pass  # best-effort; a missed beat is not worth an outage
        self._page_if_orphan_shaped()

    def _page_if_orphan_shaped(self) -> None:
        """Once per life: tell the operator BEFORE the sweep does.

        Every navig launch path goes through the scheduled task, so this fires
        only on the fallback spawn, a foreign launcher (the tray, a shell) that
        exited, or a machine where the task was never installed — exactly the
        cases `doctor`'s Daemon-parent row exists for, pushed instead of pulled.
        Checked on every beat because a parent can exit long after boot.
        """
        if self._orphan_reported:
            return
        try:
            info = NavigDaemon.parent_of(os.getpid())
        except Exception:  # noqa: BLE001
            return
        if info.get("shape") != "orphan":
            return
        self._orphan_reported = True
        try:
            from navig.core import incidents

            incidents.record(
                incidents.DAEMON_ORPHAN_SHAPED, pid=os.getpid(), parent_pid=info.get("ppid")
            )
        except Exception:  # noqa: BLE001 — a page must never break the loop
            pass

    @staticmethod
    def last_seen_alive() -> str | None:
        """ISO-UTC time of the last heartbeat (``state.json`` mtime), or None.

        None means there is no state file — a cleanly stopped daemon removes it.
        """
        try:
            return datetime.fromtimestamp(_state_file().stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            return None

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

        # A sibling supervisor that started moments BEFORE us is not a stale
        # generation — it is a concurrent boot (2026-09-21: the task launched one,
        # then `service restart` spawned a second while the first was still in
        # its own sweep). The older boot is the one with the living parent; we
        # yield to it rather than kill it.
        # One process-table enumeration (WMI, 10–20 s here) serves both the
        # sibling check and the sweep — a second one would double the boot.
        candidates = self._enumerate_navig_pids()
        sibling = self._booting_sibling(candidates)
        if sibling is not None:
            self.logger.info(
                "Another daemon (pid=%d) started just before us and is still booting — "
                "yielding to it",
                sibling,
            )
            return

        # Sweep stale daemon generations from previous restarts before
        # writing the new PID file so their log handles are released. Never a
        # process younger than us: that is a concurrent boot, and it yields
        # (above) on its own — sweeping it too is how two boots killed each
        # other.
        swept = self._kill_orphan_daemons(
            exclude_pid=os.getpid(), only_older_than=_own_create_time(), pids=candidates
        )
        if swept:
            self.logger.info("Swept %d orphan daemon PID(s): %s", len(swept), swept)

        self._running = True
        self._started_at = datetime.now(timezone.utc).isoformat()
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

                self._heartbeat()
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
            # The docstring promised "+ its tree" and POSIX killed only the pid: the
            # gateway/bot children survived as orphans holding their ports. Snapshot the
            # descendants FIRST — once the parent is dead the tree cannot be walked.
            from navig.core.aio_subprocess import _snapshot_descendants

            descendants = _snapshot_descendants(pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            for child in descendants:
                try:
                    child.kill()
                except Exception:  # noqa: BLE001 — a vanished child is the good case
                    pass

    #: A supervisor-shaped sibling that started within this many seconds before us
    #: is treated as a concurrent boot, not a stale generation. The boot sweep is
    #: WMI-bound (~10–20 s measured); a minute covers a loaded machine.
    BOOT_GRACE_S: float = 90.0

    @staticmethod
    def booting_supervisor_pids(
        *, config_dir: Path | None = None, candidates: list[int] | None = None
    ) -> list[tuple[int, float]]:
        """``(pid, create_time)`` of every OTHER supervisor process of OUR brain.

        Supervisor-shaped means the cmdline runs ``navig.daemon.entry`` (or the
        ``-m navig.daemon`` form) — not a gateway or telegram worker child. Scoped by
        config dir like every kill path; an unreadable process is not ours. Empty
        without psutil or on any failure. *candidates* lets a caller that already
        enumerated the process table (a WMI call) hand it in instead of paying twice.
        """
        try:
            import psutil  # type: ignore[import-untyped]

            from navig.daemon.single_instance import config_dir_of
            from navig.platform import paths

            mine = (config_dir if config_dir is not None else paths.config_dir()).resolve()
        except Exception:  # noqa: BLE001
            return []
        out: list[tuple[int, float]] = []
        pids = candidates if candidates is not None else NavigDaemon._enumerate_navig_pids()
        for pid in pids:
            if pid == os.getpid():
                continue
            try:
                proc = psutil.Process(pid)
                cmd = " ".join(proc.cmdline() or []).lower()
                if "navig.daemon.entry" not in cmd and "-m navig.daemon" not in cmd:
                    continue
                if "telegram_worker" in cmd or "gateway start" in cmd:
                    continue
                theirs = config_dir_of(pid)
                if theirs is None or Path(theirs).resolve() != mine:
                    continue
                out.append((pid, float(proc.create_time())))
            except Exception:  # noqa: BLE001 — gone, or not ours to read
                continue
        return out

    def _booting_sibling(self, candidates: list[int] | None = None) -> int | None:
        """The pid of a supervisor that started within ``BOOT_GRACE_S`` BEFORE us, if any."""
        ours = _own_create_time()
        if ours is None:
            return None
        for pid, created in self.booting_supervisor_pids(candidates=candidates):
            if ours - self.BOOT_GRACE_S <= created < ours:
                return pid
        return None

    @staticmethod
    def _kill_orphan_daemons(
        exclude_pid: int | None = None,
        *,
        config_dir: Path | None = None,
        config_dir_reader=None,
        pids: list[int] | None = None,
        killer=None,
        keep: set[int] | None = None,
        only_older_than: float | None = None,
    ) -> list[int]:
        """Force-kill stale navig daemon/gateway/worker processes **for OUR brain only**.

        ``only_older_than`` (a ``psutil`` create_time): skip any candidate created at
        or after that instant. The boot sweep passes its own start time, so a
        concurrent boot that began after us is never a casualty — it yields on
        its own. Without psutil the filter is a no-op (the previous behaviour).

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
            if only_older_than is not None and not _created_before(found_pid, only_older_than):
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

