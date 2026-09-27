"""ONE policy for "run the bot in the background".

Five commands used to spawn a bare ``telegram_worker`` detached from the CLI —
``navig bot start --background``, ``navig start``, ``navig agent telegram`` — each with
its own copy of the same ``Popen`` block. Every one of those workers was
orphan-shaped (no living parent the moment the CLI exited) and wrote no pid file,
so it was invisible to ``navig service pids`` and to the process sweeper that
reads it; on this operator's machine that shape is killed every hour at :01.

The daemon already solves all of that: the supervisor is task-launched (a living
parent), restarts a dead child, keeps a heartbeat, and owns the pid contract. So
a background bot launch now prefers it, in order:

1. The supervisor is running and runs the bot → nothing to start; say so.
2. The service is installed → start it (``navig service start``), which on
   Windows relaunches THROUGH the scheduled task.
3. Otherwise → the direct spawn, unchanged — but the worker writes
   ``<config_dir>/worker.pid`` at boot, so it is listed by ``service pids`` and
   spared by a sweeper that follows the contract.

Nothing here changes what the commands mean; it changes what carries them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from navig.platform import paths

WORKER_PID_NAME = "worker.pid"


def worker_pid_file() -> Path:
    """``<config_dir>/worker.pid`` — the standalone worker's identity.

    Written by the WORKER itself at boot (so ``pid_from_pidfile``'s "the owner is
    older than the file" check holds), removed on a clean exit. Under the
    supervisor the worker is already a descendant of ``supervisor.pid`` and is
    not listed twice.
    """
    return paths.config_dir() / WORKER_PID_NAME


def write_worker_pid() -> None:
    """Best-effort: an unwritable pid file must never stop the bot."""
    try:
        pf = worker_pid_file()
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def remove_worker_pid() -> None:
    """Only if it still names US — a newer worker may own it by now."""
    try:
        pf = worker_pid_file()
        if pf.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pf.unlink(missing_ok=True)
    except OSError:
        pass


def supervisor_runs_the_bot() -> int | None:
    """The supervisor's pid when it is running AND its config runs the bot."""
    try:
        from navig.daemon.entry import _load_config
        from navig.daemon.supervisor import NavigDaemon

        if not NavigDaemon.is_running():
            return None
        cfg = _load_config()
        if not cfg.get("telegram_bot", True):
            return None
        return NavigDaemon.read_pid()
    except Exception:  # noqa: BLE001 — a probe must never block a launch
        return None


def service_is_installed() -> bool:
    """Is there an installed service to start THROUGH — a living parent?

    Windows: the scheduled task exists. POSIX: the systemd unit file exists.
    ``None``/unknown counts as "no": a launch must not wait on a probe.
    """
    try:
        from navig.daemon import service_manager as sm

        if sys.platform == "win32":
            _enabled, installed, _detail = sm.task_scheduler_enabled_state()
            return installed is True
        if sm.has_launchd():
            return sm._launchd_plist_path().exists()
        if sm.has_systemd():
            return sm._systemd_unit_path(user=True).exists() or sm._systemd_unit_path().exists()
    except Exception:  # noqa: BLE001
        return False
    return False


def spawn_detached(cmd: list[str]) -> subprocess.Popen:
    """The direct spawn every command used to inline — kept in one place."""
    if sys.platform == "win32":
        return subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def supervisor_runs_the_gateway() -> int | None:
    """The supervisor's pid when it is running AND its config runs the gateway."""
    try:
        from navig.daemon.entry import _load_config
        from navig.daemon.supervisor import NavigDaemon

        if not NavigDaemon.is_running():
            return None
        if not _load_config().get("gateway", False):
            return None
        return NavigDaemon.read_pid()
    except Exception:  # noqa: BLE001
        return None


def start_gateway_in_background(*, port: int | None, host: str | None, ch: Any) -> str:
    """`navig gateway start --background`, which used to print "not yet implemented"
    and run in the FOREGROUND. Same ladder as the bot: the running daemon, the
    installed service, else a detached `navig gateway start` — which writes
    `gateway.pid` itself, so the pid contract already covers it."""
    sup = supervisor_runs_the_gateway()
    if sup:
        ch.info(f"The gateway already runs under the daemon (supervisor pid={sup}) — nothing to start")
        ch.dim("  navig service status · navig service restart to reload it")
        return "daemon-running"

    if service_is_installed():
        try:
            from navig.daemon.entry import _load_config

            gateway_enabled = bool(_load_config().get("gateway", False))
        except Exception:  # noqa: BLE001
            gateway_enabled = False
        if gateway_enabled:
            ch.info("Starting through the installed service — a living parent, autostart, a pid contract")
            from navig.commands.service import service_start

            service_start(foreground=False, logs=False)
            return "service"

    cmd = [sys.executable, "-m", "navig", "gateway", "start"]
    if port is not None:
        cmd += ["--port", str(port)]
    if host is not None:
        cmd += ["--host", host]
    proc = spawn_detached(cmd)
    ch.success(f"Gateway started in background (pid={getattr(proc, 'pid', '?')})")
    ch.dim(
        "  Detached from this terminal: no parent, no auto-restart. "
        "For a supervised gateway: navig service install --gateway"
    )
    return "spawned"


def start_bot_in_background(*, gateway: bool, port: int | None, ch: Any) -> str:
    """Run the bot in the background the most durable way available.

    Returns how it was done: ``"daemon-running"`` · ``"service"`` · ``"spawned"``.
    ``ch`` is the console helper (injected so the policy is testable without Rich).
    """
    sup = supervisor_runs_the_bot()
    if sup:
        ch.info(f"The bot already runs under the daemon (supervisor pid={sup}) — nothing to start")
        ch.dim("  navig service status · navig service restart to reload it")
        return "daemon-running"

    if service_is_installed():
        ch.info(
            "Starting through the installed service — a living parent, autostart, a pid contract"
        )
        from navig.commands.service import service_start

        service_start(foreground=False, logs=False)
        return "service"

    cmd = [sys.executable, "-m", "navig.daemon.telegram_worker"]
    if gateway:
        if port is None:
            from navig.commands.gateway import _load_gateway_cli_defaults

            port, _host = _load_gateway_cli_defaults()
        cmd += ["--port", str(port)]
    else:
        cmd.append("--no-gateway")
    proc = spawn_detached(cmd)
    ch.success(f"Started in background (pid={getattr(proc, 'pid', '?')})")
    ch.dim(
        "  Detached from this terminal: no parent, no auto-restart. "
        "For a supervised bot: navig service install"
    )
    return "spawned"
