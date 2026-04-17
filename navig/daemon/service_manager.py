"""
NAVIG Service Manager — cross-platform service installation.

Supports multiple backends:
  1. systemd (Linux — recommended)
  2. NSSM    (Windows, recommended if admin + nssm available)
  3. Task Scheduler (Windows, no admin needed for "on login" tasks)
  4. Manual instructions as fallback

All methods ultimately wrap the same command:
    python -m navig.daemon.entry

So the supervisor daemon starts and manages subsystems internally.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import ctypes
except ImportError:
    ctypes = None

from navig.core.yaml_io import atomic_write_text
from navig.platform import paths

NAVIG_HOME = paths.config_dir()
LOG_DIR = NAVIG_HOME / "logs"
DAEMON_DIR = NAVIG_HOME / "daemon"
SERVICE_NAME = "NavigDaemon"
TASK_NAME = "NAVIG Daemon"
SYSTEMD_UNIT = "navig-agent"  # Linux systemd unit name


def _python_exe() -> str:
    return sys.executable


def _pythonw_exe() -> str:
    """Return pythonw.exe path (windowless) if available, else python.exe."""
    if sys.platform == "win32":
        pw = Path(sys.executable).parent / "pythonw.exe"
        if pw.exists():
            return str(pw)
    return sys.executable


def _daemon_command(*, windowless: bool = True) -> list[str]:
    """The command that launches the supervised daemon."""
    exe = _pythonw_exe() if windowless else _python_exe()
    return [exe, "-m", "navig.daemon.entry"]


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def has_nssm() -> bool:
    return shutil.which("nssm") is not None


def is_admin() -> bool:
    """Check if running with administrator privileges (Windows)."""
    if sys.platform != "win32":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        if ctypes is None:
            return False
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# NSSM backend
# ---------------------------------------------------------------------------


def nssm_install(start_now: bool = True) -> tuple[bool, str]:
    """Install the daemon as a Windows service via NSSM."""
    _ensure_dirs()
    cmd = _daemon_command(windowless=True)
    python = cmd[0]
    args = " ".join(cmd[1:])

    try:
        # Install
        subprocess.run(
            ["nssm", "install", SERVICE_NAME, python, args],
            check=True,
            capture_output=True,
        )
        # Set working directory
        subprocess.run(
            ["nssm", "set", SERVICE_NAME, "AppDirectory", str(NAVIG_HOME)],
            capture_output=True,
        )
        # Description
        subprocess.run(
            [
                "nssm",
                "set",
                SERVICE_NAME,
                "Description",
                "NAVIG persistent daemon (bot + gateway + scheduler)",
            ],
            capture_output=True,
        )
        # Auto-start
        subprocess.run(
            ["nssm", "set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"],
            capture_output=True,
        )
        # Stdout / Stderr logs
        subprocess.run(
            [
                "nssm",
                "set",
                SERVICE_NAME,
                "AppStdout",
                str(LOG_DIR / "service.stdout.log"),
            ],
            capture_output=True,
        )
        subprocess.run(
            [
                "nssm",
                "set",
                SERVICE_NAME,
                "AppStderr",
                str(LOG_DIR / "service.stderr.log"),
            ],
            capture_output=True,
        )
        # Restart on crash
        subprocess.run(
            ["nssm", "set", SERVICE_NAME, "AppExit", "Default", "Restart"],
            capture_output=True,
        )
        # Environment: pass current env + NAVIG markers
        env_str = f"NAVIG_SERVICE=1\nNAVIG_HOME={NAVIG_HOME}"
        subprocess.run(
            ["nssm", "set", SERVICE_NAME, "AppEnvironmentExtra", env_str],
            capture_output=True,
        )

        if start_now:
            subprocess.run(["nssm", "start", SERVICE_NAME], check=True, capture_output=True)
            return True, f"Service '{SERVICE_NAME}' installed and started via NSSM"
        return True, f"Service '{SERVICE_NAME}' installed via NSSM (not started)"

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        return False, f"NSSM install failed: {err}"


def nssm_uninstall() -> tuple[bool, str]:
    try:
        subprocess.run(["nssm", "stop", SERVICE_NAME], capture_output=True)
        subprocess.run(["nssm", "remove", SERVICE_NAME, "confirm"], check=True, capture_output=True)
        return True, "Service removed via NSSM"
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        return False, f"NSSM uninstall failed: {err}"


def nssm_status() -> tuple[bool, str]:
    try:
        result = subprocess.run(["nssm", "status", SERVICE_NAME], capture_output=True, text=True)
        running = "SERVICE_RUNNING" in result.stdout
        return running, result.stdout.strip()
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Task Scheduler backend (no admin required for "on login")
# ---------------------------------------------------------------------------


def _schtasks_xml() -> str:
    """Generate a Task Scheduler XML definition."""
    cmd = _daemon_command(windowless=True)
    python = cmd[0]
    args = " ".join(cmd[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>NAVIG persistent daemon — Telegram bot, gateway, scheduler</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions>
    <Exec>
      <Command>{python}</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{NAVIG_HOME}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def task_scheduler_install(start_now: bool = True) -> tuple[bool, str]:
    """Install via Windows Task Scheduler (no admin needed)."""
    _ensure_dirs()
    xml_path = DAEMON_DIR / "navig-task.xml"
    xml_path.write_text(_schtasks_xml(), encoding="utf-16")

    try:
        subprocess.run(
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(xml_path), "/f"],
            check=True,
            capture_output=True,
        )
        if start_now:
            subprocess.run(
                ["schtasks", "/run", "/tn", TASK_NAME],
                check=True,
                capture_output=True,
            )
            return True, f"Task '{TASK_NAME}' created and started"
        return True, f"Task '{TASK_NAME}' created (will start on next login)"
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        if "acc" in err.lower() or "refus" in err.lower() or "denied" in err.lower():
            return False, (
                "Task Scheduler requires Administrator rights.\n"
                "  → Right-click your terminal and choose 'Run as administrator', then retry:\n"
                "    navig service install --bot"
            )
        return False, f"Task Scheduler failed: {err}"


def task_scheduler_end() -> tuple[bool, str]:
    """Terminate the currently-running task instance via the Task Scheduler.

    ``schtasks /end`` signals the scheduler service to call TerminateProcess
    on the process it spawned for the task's current run.  This kills the
    supervisor (daemon.entry) process directly.  It is a no-op when no
    instance is running and succeeds (returncode 0) when the task is not
    installed.
    """
    try:
        r = subprocess.run(
            ["schtasks", "/end", "/tn", TASK_NAME],
            capture_output=True,
        )
        if r.returncode != 0:
            # Task not installed or no running instance — treat as success.
            return False, (r.stderr or r.stdout or b"").decode("utf-8", errors="replace").strip()
        return True, f"Task '{TASK_NAME}' instance ended"
    except Exception as e:
        return False, str(e)


def task_scheduler_disable() -> tuple[bool, str]:
    """Disable the scheduled task so it cannot auto-restart the daemon.

    Call this *before* killing the daemon process so that the
    RestartOnFailure policy cannot relaunch it within the next minute.
    """
    try:
        # /change /disable prevents triggers AND RestartOnFailure from firing.
        r = subprocess.run(
            ["schtasks", "/change", "/tn", TASK_NAME, "/disable"],
            capture_output=True,
        )
        if r.returncode != 0:
            # Task may not exist (not installed via task scheduler).
            return False, (r.stderr or r.stdout or b"").decode("utf-8", errors="replace").strip()
        return True, f"Task '{TASK_NAME}' disabled"
    except Exception as e:
        return False, str(e)


def task_scheduler_enable() -> tuple[bool, str]:
    """Re-enable the scheduled task after the daemon has been (re)started."""
    try:
        r = subprocess.run(
            ["schtasks", "/change", "/tn", TASK_NAME, "/enable"],
            capture_output=True,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or b"").decode("utf-8", errors="replace").strip()
        return True, f"Task '{TASK_NAME}' enabled"
    except Exception as e:
        return False, str(e)


def task_scheduler_uninstall() -> tuple[bool, str]:
    try:
        subprocess.run(
            ["schtasks", "/end", "/tn", TASK_NAME],
            capture_output=True,
        )
        subprocess.run(
            ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
            check=True,
            capture_output=True,
        )
        return True, "Scheduled task removed"
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        return False, f"Task Scheduler uninstall failed: {err}"


def task_scheduler_status() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST", "/v"],
            capture_output=True,
            text=True,
        )
        detail = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            return False, detail or "Task Scheduler query failed"
        running = "running" in (result.stdout or "").lower()
        return running, detail
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# systemd backend (Linux)
# ---------------------------------------------------------------------------


def has_systemd() -> bool:
    """Check if systemd is available on this system."""
    return shutil.which("systemctl") is not None


def _systemd_unit_path(user: bool = False) -> Path:
    """Path to the systemd unit file."""
    if user:
        config_dir = Path.home() / ".config" / "systemd" / "user"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / f"{SYSTEMD_UNIT}.service"
    return Path(f"/etc/systemd/system/{SYSTEMD_UNIT}.service")


def _systemd_unit_content(user: bool = False) -> str:
    """Generate a systemd unit file for the NAVIG agent daemon."""
    python = _python_exe()
    home = str(NAVIG_HOME)
    log_path = str(LOG_DIR / "daemon.log")

    unit = f"""[Unit]
Description=NAVIG Agent Daemon
Documentation=https://github.com/navig-run/core
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m navig.daemon.entry
WorkingDirectory={home}
Restart=on-failure
RestartSec=10
StandardOutput=append:{log_path}
StandardError=append:{log_path}
Environment=NAVIG_SERVICE=1
Environment=NAVIG_HOME={home}
"""
    if not user:
        import getpass

        username = getpass.getuser()
        unit += f"User={username}\n"
        unit += f"Group={username}\n"

    unit += """
[Install]
WantedBy="""
    unit += "default.target\n" if user else "multi-user.target\n"
    return unit


def systemd_install(start_now: bool = True) -> tuple[bool, str]:
    """Install the daemon as a systemd service."""
    _ensure_dirs()

    # Decide: system-wide (needs root) or user service
    use_sudo = is_admin()
    user_mode = not use_sudo

    unit_path = _systemd_unit_path(user=user_mode)
    unit_content = _systemd_unit_content(user=user_mode)

    try:
        if user_mode:
            # User-level service — no sudo needed
            atomic_write_text(unit_path, unit_content)
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "--user", "enable", SYSTEMD_UNIT],
                check=True,
                capture_output=True,
            )
            # Enable lingering so user services run without login
            subprocess.run(
                ["loginctl", "enable-linger"],
                capture_output=True,
            )
            if start_now:
                subprocess.run(
                    ["systemctl", "--user", "start", SYSTEMD_UNIT],
                    check=True,
                    capture_output=True,
                )
                return True, f"User service '{SYSTEMD_UNIT}' installed and started"
            return True, f"User service '{SYSTEMD_UNIT}' installed (not started)"
        else:
            # System-wide service — running as root
            atomic_write_text(unit_path, unit_content)
            subprocess.run(
                ["systemctl", "daemon-reload"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "enable", SYSTEMD_UNIT],
                check=True,
                capture_output=True,
            )
            if start_now:
                subprocess.run(
                    ["systemctl", "start", SYSTEMD_UNIT],
                    check=True,
                    capture_output=True,
                )
                return True, f"System service '{SYSTEMD_UNIT}' installed and started"
            return True, f"System service '{SYSTEMD_UNIT}' installed (not started)"

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        return False, f"systemd install failed: {err}"
    except PermissionError:
        return False, (
            "Permission denied writing unit file. "
            "Run with sudo for system-wide install, or it will use user mode."
        )


def systemd_uninstall() -> tuple[bool, str]:
    """Remove the systemd service."""
    try:
        # Try system-wide first, then user
        system_unit = _systemd_unit_path(user=False)
        user_unit = _systemd_unit_path(user=True)

        if system_unit.exists() and is_admin():
            subprocess.run(["systemctl", "stop", SYSTEMD_UNIT], capture_output=True)
            subprocess.run(["systemctl", "disable", SYSTEMD_UNIT], capture_output=True)
            system_unit.unlink(missing_ok=True)
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
            return True, f"System service '{SYSTEMD_UNIT}' removed"
        elif user_unit.exists():
            subprocess.run(["systemctl", "--user", "stop", SYSTEMD_UNIT], capture_output=True)
            subprocess.run(["systemctl", "--user", "disable", SYSTEMD_UNIT], capture_output=True)
            user_unit.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            return True, f"User service '{SYSTEMD_UNIT}' removed"
        else:
            return False, f"No systemd unit found for '{SYSTEMD_UNIT}'"
    except Exception as e:
        return False, f"systemd uninstall failed: {e}"


def systemd_status() -> tuple[bool, str]:
    """Check systemd service status."""
    try:
        # Try system-wide first
        result = subprocess.run(
            ["systemctl", "is-active", SYSTEMD_UNIT],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            detail = subprocess.run(
                ["systemctl", "status", SYSTEMD_UNIT, "--no-pager", "-l"],
                capture_output=True,
                text=True,
            )
            return True, detail.stdout.strip()

        # Try user service
        result = subprocess.run(
            ["systemctl", "--user", "is-active", SYSTEMD_UNIT],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            detail = subprocess.run(
                ["systemctl", "--user", "status", SYSTEMD_UNIT, "--no-pager", "-l"],
                capture_output=True,
                text=True,
            )
            return True, detail.stdout.strip()

        return False, "Service is not active"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Unified API
# ---------------------------------------------------------------------------


def detect_best_method() -> str:
    """Pick the best available installation method for the current platform."""
    if sys.platform != "win32":
        # Linux / macOS
        if has_systemd():
            return "systemd"
        return "manual"
    # Windows
    if has_nssm() and is_admin():
        return "nssm"
    return "task"


def install(method: str | None = None, start_now: bool = True) -> tuple[bool, str]:
    """Install NAVIG daemon as a persistent service."""
    if method is None:
        method = detect_best_method()
    else:
        method = method.strip().lower()
    if method == "nssm":
        if not has_nssm():
            return (
                False,
                "NSSM not found. Install from https://nssm.cc/download or use --method task",
            )
        if not is_admin():
            return (
                False,
                "NSSM requires administrator privileges. Run as admin or use --method task",
            )
        return nssm_install(start_now)
    elif method == "task":
        return task_scheduler_install(start_now)
    elif method == "systemd":
        if sys.platform == "win32":
            return False, "systemd is not available on Windows. Use 'nssm' or 'task'"
        if not has_systemd():
            return False, "systemd not found on this system"
        return systemd_install(start_now)
    elif method == "manual":
        return False, (
            "No supported service manager found.\n"
            "Run manually: python -m navig.daemon.entry\n"
            "Or create a systemd/supervisor unit pointing at that command."
        )
    else:
        return False, f"Unknown method: {method}. Use 'nssm', 'task', or 'systemd'"


def uninstall(method: str | None = None) -> tuple[bool, str]:
    """Remove NAVIG daemon service."""
    if method is not None:
        method = method.strip().lower()

    if method == "nssm":
        return nssm_uninstall()
    if method == "task":
        return task_scheduler_uninstall()
    if method == "systemd":
        return systemd_uninstall()

    # Auto mode: uninstall any known backend that might be installed,
    # instead of relying on a single best-method detection.
    attempts: list[tuple[str, tuple[bool, str]]] = []
    if sys.platform == "win32":
        if has_nssm():
            attempts.append(("nssm", nssm_uninstall()))
        attempts.append(("task", task_scheduler_uninstall()))
    else:
        if has_systemd():
            attempts.append(("systemd", systemd_uninstall()))

    successes = [f"{backend}: {msg}" for backend, (ok, msg) in attempts if ok]
    if successes:
        return True, "\n".join(successes)

    if method is None and attempts:
        failures = [f"{backend}: {msg}" for backend, (_ok, msg) in attempts]
        return False, "No installed service backend could be removed.\n" + "\n".join(failures)

    if method is None:
        return False, "No supported service backend found"
    return False, f"Unknown method: {method}"


def status(method: str | None = None) -> tuple[bool, str]:
    """Check NAVIG daemon service status."""
    from navig.daemon.supervisor import NavigDaemon

    if method is not None:
        method = method.strip().lower()

    def _summary_line(detail: str | None) -> str | None:
        if not detail:
            return None
        for raw_line in detail.splitlines():
            line = raw_line.strip()
            if line:
                return " ".join(line.split())
        return None

    daemon_running = NavigDaemon.is_running()
    daemon_pid = NavigDaemon.read_pid()

    lines = []
    lines.append(f"Daemon process: {'RUNNING' if daemon_running else 'STOPPED'}")
    if daemon_pid:
        lines.append(f"  PID: {daemon_pid}")

    state = NavigDaemon.read_state()
    if state and daemon_running:
        for child in state.get("children", []):
            status_str = "ALIVE" if child.get("alive") else "DEAD"
            lines.append(
                f"  {child['name']}: {status_str} (pid={child.get('pid', '?')}, restarts={child.get('restart_count', 0)})"
            )

    # Platform-specific service checks
    if sys.platform == "win32":
        if method in (None, "nssm") and has_nssm():
            running_ns, detail_ns = nssm_status()
            lines.append(f"NSSM service: {'Active' if running_ns else 'Inactive'}")
            summary = _summary_line(detail_ns)
            if summary:
                lines.append(f"  Detail: {summary}")

        if method in (None, "task"):
            running_ts, detail_ts = task_scheduler_status()
            lines.append(f"Task Scheduler: {'Active' if running_ts else 'Inactive'}")
            summary = _summary_line(detail_ts)
            if summary:
                lines.append(f"  Detail: {summary}")
    else:
        if method in (None, "systemd") and has_systemd():
            running_sd, detail_sd = systemd_status()
            lines.append(f"systemd unit ({SYSTEMD_UNIT}): {'Active' if running_sd else 'Inactive'}")
            summary = _summary_line(detail_sd)
            if summary:
                lines.append(f"  Detail: {summary}")

    return daemon_running, "\n".join(lines)
