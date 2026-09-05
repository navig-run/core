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

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import ctypes
except ImportError:
    ctypes = None

from navig.core.proc_text import console_encoding
from navig.core.yaml_io import atomic_write_text
from navig.platform import paths

SERVICE_NAME = "NavigDaemon"
TASK_NAME = "NAVIG Daemon"
SYSTEMD_UNIT = "navig-agent"  # Linux systemd unit name


# Paths are resolved at CALL time (config_dir() honours NAVIG_CONFIG_DIR) —
# frozen module constants would point service install/stop state at the real
# user home before test/daemon isolation applies
# (see navig/vault/migrate.py:_legacy_db_path).
def _navig_home() -> Path:
    return paths.config_dir()


def _log_dir() -> Path:
    return _navig_home() / "logs"


def daemon_dir() -> Path:
    """Public call-time resolver for the daemon state directory."""
    return _navig_home() / "daemon"


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
    _log_dir().mkdir(parents=True, exist_ok=True)
    daemon_dir().mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Stop-intent flag
# ---------------------------------------------------------------------------

# Written by service_stop() before sweeping orphans.  Checked by
# daemon/entry.py on startup: if the flag exists the daemon refuses to start
# so that external "watchers" (tray apps, startup scripts) that call
# `navig service start` or spawn the daemon directly are blocked until a
# deliberate `navig service start` clears it.

def _stop_flag_path() -> Path:
    return daemon_dir() / "stop_requested"


def _watchdog_deadline_path() -> Path:
    return daemon_dir() / "stop_watchdog_deadline"


def set_stop_flag() -> None:
    """Create the stop-intent flag so the daemon refuses to auto-restart."""
    try:
        flag = _stop_flag_path()
        flag.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(flag, "1")
    except Exception:  # noqa: BLE001
        pass  # best-effort; never block the stop path


def clear_stop_flag() -> None:
    """Remove the stop-intent flag so the daemon is allowed to start."""
    try:
        _stop_flag_path().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def stop_flag_is_set() -> bool:
    """Return True if a deliberate stop has been requested."""
    return _stop_flag_path().exists()


# ---------------------------------------------------------------------------
# Watchdog deadline helpers
# ---------------------------------------------------------------------------

def set_watchdog_deadline(seconds: int = 30) -> None:
    """Write a UNIX-timestamp deadline for the orphan-kill watchdog.

    The watchdog reads this file and loops until ``time.time()`` exceeds the
    deadline OR the file is deleted.  Using a separate file (not the stop-intent
    flag) means the watchdog is immune to external callers that clear the stop
    flag via ``clear_stop_flag()``.
    """
    import time as _time

    try:
        deadline = _watchdog_deadline_path()
        deadline.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(deadline, str(_time.time() + seconds))
    except Exception:  # noqa: BLE001
        pass


def clear_watchdog_deadline() -> None:
    """Delete the watchdog deadline file, causing the watchdog to exit on its next tick."""
    try:
        _watchdog_deadline_path().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def watchdog_deadline_active() -> bool:
    """Return True if the stop-watchdog deadline is still in the future.

    Used by ``daemon/entry.py`` to refuse auto-restarts during the watchdog window.
    """
    import time as _time

    try:
        val = float(_watchdog_deadline_path().read_text(encoding="utf-8").strip())
        return _time.time() < val
    except Exception:  # noqa: BLE001
        return False


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
            ["nssm", "set", SERVICE_NAME, "AppDirectory", str(_navig_home())],
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
                str(_log_dir() / "service.stdout.log"),
            ],
            capture_output=True,
        )
        subprocess.run(
            [
                "nssm",
                "set",
                SERVICE_NAME,
                "AppStderr",
                str(_log_dir() / "service.stderr.log"),
            ],
            capture_output=True,
        )
        # Restart on crash
        subprocess.run(
            ["nssm", "set", SERVICE_NAME, "AppExit", "Default", "Restart"],
            capture_output=True,
        )
        # Environment: pass current env + NAVIG markers.
        # NAVIG_CONFIG_DIR is the CANONICAL var config_dir() (and vault, gateway.json, the
        # single-instance/supersede scoping — everything downstream) reads; NAVIG_HOME is a
        # legacy alias only memory/paths + theme honour. Writing NAVIG_HOME alone split the
        # daemon: with a custom install home, config_dir() fell back to the default ~/.navig
        # while memory followed NAVIG_HOME. Both are written to the SAME value, so every
        # reader resolves the same home and no divergence is possible.
        _home = _navig_home()
        env_str = f"NAVIG_SERVICE=1\nNAVIG_CONFIG_DIR={_home}\nNAVIG_HOME={_home}"
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


def _task_env_setup(home: Path) -> str:
    """The env assignments the scheduled task bakes in, as executable Python.

    Split out so a test can execute exactly these — and only these — without also
    running the log redirection, which would hijack the test session's stdout.
    """
    h = str(home).replace("\\", "/")
    return (
        "os.environ['NAVIG_SERVICE']='1'; "
        f"os.environ['NAVIG_CONFIG_DIR']='{h}'; "
        f"os.environ.setdefault('NAVIG_HOME', '{h}'); "
    )


def _task_bootstrap_args(home: Path) -> str:
    """The `-c` bootstrap the scheduled task runs.

    A Task Scheduler `<Exec>` action has NO environment mechanism (unlike systemd's
    `Environment=` or NSSM's AppEnvironmentExtra) — the task inherits only the user's
    PERSISTENT env, not whatever `NAVIG_CONFIG_DIR` the install shell had. So a daemon
    installed with a custom home ran its config/vault/gateway/supersede against the default
    ~/.navig while only memory followed the custom home (the #302 split brain, through the
    Windows fallback door).

    Rather than a wrapper .cmd (console flash) or `setx` (pollutes the user's global env),
    the task launches `pythonw -c <bootstrap>`, which sets the vars in os.environ BEFORE any
    navig import, then hands off to the daemon module exactly as `-m navig.daemon.entry`
    would. Forward-slash the home (Windows accepts it) to avoid a trailing-backslash killing
    the string literal; NAVIG_HOME uses setdefault so an explicit ambient value still wins.

    **stdout/stderr are pointed at daemon/boot.log first.** `pythonw` has no console: its
    streams are None, so a boot that fails leaves the task with `Last Result: 1` and not one
    byte of evidence anywhere — which is exactly how an installed autostart delivered nothing
    for a day without anyone being able to say why. A file the boot can talk to costs one
    open() and turns the next silent failure into a readable line. Opened line-buffered and
    append-only; a failure to open it must never stop the daemon from starting.
    """
    h = str(home).replace("\\", "/")
    return (
        f'-c "import os, sys; '
        f"{_task_env_setup(home)}"
        f"os.makedirs(r'{h}/daemon', exist_ok=True); "
        f"_f=open(r'{h}/daemon/boot.log', 'a', encoding='utf-8', buffering=1); "
        f"sys.stdout=sys.stderr=_f; "
        f"import runpy; runpy.run_module('navig.daemon.entry', run_name='__main__', alter_sys=True)\""
    )


def _schtasks_xml() -> str:
    """Generate a Task Scheduler XML definition."""
    from xml.sax.saxutils import escape as _xml_escape

    home = _navig_home()
    python = _xml_escape(_pythonw_exe())
    workdir = _xml_escape(str(home))
    # The task has no env mechanism, so bake NAVIG_CONFIG_DIR into the launch (see
    # _task_bootstrap_args). XML-escape everything interpolated — a username with '&' would
    # otherwise produce malformed XML (the WorkingDirectory + Command were previously raw).
    args = _xml_escape(_task_bootstrap_args(home))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>NAVIG persistent daemon — Telegram bot, gateway, scheduler</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
    <!-- A repeating trigger, deliberately, and ONLY because the bootstrap is now
         idempotent. Read this before changing either half.

         The daemon detaches, so the process Task Scheduler launches exits at once
         and the instance is marked COMPLETE. RestartOnFailure therefore never
         applies, and a LogonTrigger does not fire again while the user stays
         logged in. Net effect: a dead daemon stayed dead, and an operator lost
         days of daily check-ins with every status light green.

         This trigger was withheld once, on measurement: with supervisor 8732
         healthy and serving, two further firings produced supervisors 8968 and
         75516, and daemon/state.json showed the NEWCOMER had taken over the pid
         file. Three supervisors and two gateways, growing every interval. A task
         that cannot recover beat one that multiplies daemons.

         What changed: navig.daemon.entry now asks NavigDaemon.is_running() and
         RETURNS (exit 0, so the launcher records success) when a daemon is
         already up. Verified on a live install by running this very task with
         the daemon serving: LastTaskResult 0, and the process table still held
         exactly the original supervisor and gateway.

         PT5M is measured, not guessed: a no-op duplicate launch costs about 0.6s,
         so 288 firings a day is roughly 3 minutes of CPU, bounding recovery
         latency at 5 minutes. The check-ins this protects run at fixed times.

         If you ever remove the idempotency guard in navig/daemon/entry.py, remove
         this trigger in the SAME change. tests/daemon/test_autostart_watchdog.py
         pins them together so that cannot be done by accident. -->
    <TimeTrigger>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2020-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
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
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def _refuse_task_mutation(operation: str) -> tuple[bool, str] | None:
    """Block a test process from mutating the OPERATOR'S real scheduled task.

    ``TASK_NAME`` is the bare literal ``"NAVIG Daemon"`` -- there is no config-dir
    scoping on Task Scheduler the way there now is on the daemon's pid file and log
    dir, and there cannot be: the task is a machine-level object, not a file under a
    config root. So any code path that reaches ``schtasks /change ... /disable`` from
    a test disables the autostart of whoever is running the suite, silently, and it
    stays off until someone notices their daemon never came back from a reboot.

    That is the #1189 class (a test writing the operator's live daemon state) applied
    to the one remaining unscoped OS-level mutation. **Measured before adding this:
    the full `tests/service` suite (110 tests) leaves the task Ready, so this is a
    floor placed BEFORE something falls through it, not a bug report** -- but nine
    tests invoke `service stop` / `restart` / `uninstall` without stubbing these
    helpers, and they are one refactor away from arriving here.

    Reads (`task_scheduler_status`, `_health`, `_enabled_state`) are deliberately NOT
    guarded: observing the machine harms nothing, and several tests legitimately drive
    them with a stubbed ``subprocess``.

    Returns the ``(ok, detail)`` tuple every caller already returns, or ``None`` when
    the operation may proceed. Set ``NAVIG_ALLOW_TASK_MUTATION=1`` to opt in -- for a
    test that genuinely means to exercise Task Scheduler on a throwaway machine.
    """
    if "pytest" not in sys.modules:
        return None
    if os.environ.get("NAVIG_ALLOW_TASK_MUTATION") == "1":
        return None
    return False, (
        f"refusing to {operation} the '{TASK_NAME}' scheduled task from a test "
        "process: it is a machine-level object and cannot be isolated by config dir, "
        "so this would change the autostart of whoever is running the suite. Stub the "
        "helper, or set NAVIG_ALLOW_TASK_MUTATION=1 if you really mean it."
    )


def task_scheduler_install(start_now: bool = True) -> tuple[bool, str]:
    """Install via Windows Task Scheduler (no admin needed)."""
    refusal = _refuse_task_mutation("install")
    if refusal is not None:
        return refusal
    _ensure_dirs()
    xml_path = daemon_dir() / "navig-task.xml"
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
    refusal = _refuse_task_mutation("end")
    if refusal is not None:
        return refusal
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
    refusal = _refuse_task_mutation("disable")
    if refusal is not None:
        return refusal
    try:
        # /change /disable prevents triggers AND RestartOnFailure from firing.
        r = subprocess.run(
            ["schtasks", "/change", "/tn", TASK_NAME, "/disable"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode != 0:
            # Task may not exist (not installed via task scheduler).
            return False, (r.stderr or r.stdout or b"").decode("utf-8", errors="replace").strip()
        return True, f"Task '{TASK_NAME}' disabled"
    except Exception as e:
        return False, str(e)


def task_scheduler_enable() -> tuple[bool, str]:
    """Re-enable the scheduled task after the daemon has been (re)started."""
    refusal = _refuse_task_mutation("enable")
    if refusal is not None:
        return refusal
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
    refusal = _refuse_task_mutation("uninstall")
    if refusal is not None:
        return refusal
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


# Task Scheduler reports SCHED_S_* INFORMATIONAL codes through the same
# LastTaskResult field as a real exit code, so "non-zero" does not mean "failed".
#
# 267009 (0x41301, SCHED_S_TASK_RUNNING) is the one that matters here, and it is
# not transient: Task Scheduler keeps a task in the Running state for as long as
# the process it launched is alive. The daemon is long-lived, so once the watchdog
# has actually STARTED it, LastTaskResult stays 267009 for the rest of that
# daemon's life. Measured 2026-09-04: the watchdog recovered the daemon at
# 21:05:01 (parent = svchost.exe, i.e. Task Scheduler), and `navig service status`
# then read
#
#     Task Scheduler: Installed but NOT healthy
#       ! last run FAILED (result 267009)
#
# permanently — crying wolf in exactly the case where the recovery WORKED. A row
# that reports failure on success trains the operator to ignore it, which is the
# same harm as a green light over an unknown, pointed the other way.
#
# Only codes that genuinely are not a failed run are listed. A stopped task
# (0x41306 SCHED_S_TASK_TERMINATED) and the scheduling ones stay reportable.
_TASK_RESULT_NOT_A_FAILURE = frozenset({
    None,     # could not read it — the caller reports that separately
    0,        # S_OK
    267008,   # 0x41300 SCHED_S_TASK_READY      — ready, nothing wrong
    267009,   # 0x41301 SCHED_S_TASK_RUNNING    — running right now
    267011,   # 0x41303 SCHED_S_TASK_HAS_NOT_RUN — never fired yet
    # 0x800710E0 — the trigger was REFUSED because an instance was already
    # running. That is not a failure here, it is the policy working: this task
    # sets MultipleInstancesPolicy=IgnoreNew precisely so a watchdog firing
    # cannot start a second daemon. Combined with Task Scheduler holding an
    # instance "Running" for as long as the process it launched lives, and a
    # daemon that is long-lived by design, this becomes the STEADY STATE: the
    # instance that started the daemon stays Running, and every subsequent
    # 5-minute firing is refused with this code. Measured on the operator's
    # machine 2026-09-05 — a healthy install with the uplink online reported
    # "last run FAILED (result 2147946720)" on every check.
    2147946720,
})


def task_scheduler_health() -> dict:
    """Structured health of the autostart task — not just "does it exist".

    ``task_scheduler_status`` answers "is the task there", which is the question
    that let a broken autostart look fine: this operator's task sat at
    ``Last Result: 1`` with an EMPTY NextRunTime (a logon-only trigger that had
    already fired) while ``navig service status`` printed a cheerful
    ``Task Scheduler: Active``. The daemon was dead for two days and the daily
    check-ins with it. Same rule as ``navig doctor``: a green light over an
    unknown is worse than a red one.

    Read through PowerShell rather than ``schtasks /query /v`` on purpose —
    schtasks prints LOCALIZED field names and values (the reason
    ``commands/doctor.py`` reads only its exit status), so parsing it is a
    locale bug waiting to happen. ``Get-ScheduledTaskInfo`` returns PROPERTY
    names, which are the same in every locale.

    Returns ``{installed, last_result, next_run, can_recover, problems[]}``.
    Never raises; on any failure it reports ``installed=None`` (unknown), which
    callers must render as a warning rather than as health.
    """
    out: dict = {
        "installed": None, "last_result": None, "next_run": None,
        "enabled": None, "can_recover": None, "problems": [],
    }
    if sys.platform != "win32":
        return out
    try:
        ps = (
            f"$ErrorActionPreference='Stop';"
            f"$t=Get-ScheduledTask -TaskName '{TASK_NAME}';"
            f"$i=$t|Get-ScheduledTaskInfo;"
            f"[pscustomobject]@{{"
            f"last=$i.LastTaskResult;"
            f"next=$(if($i.NextRunTime){{$i.NextRunTime.ToString('o')}}else{{''}});"
            # A DISABLED task still reports a NextRunTime -- Windows computes the
            # schedule regardless of whether it will act on it. Without this field the
            # health headline read "Healthy (watchdog re-checks, next 18:20)" for a task
            # that was switched off, with "installed but DISABLED" demoted to a detail
            # line underneath. Observed on the operator's own machine.
            f"enabled=[bool]$t.Settings.Enabled;"
            f"reps=@($t.Triggers|Where-Object{{$_.Repetition.Interval}}).Count"
            f"}}|ConvertTo-Json -Compress"
        )
        # console_encoding(), not text=True: powershell writes the console code
        # page while text mode decodes with the ANSI one, so a task name or path
        # with a non-ASCII character would mojibake. Same convention as
        # task_scheduler_status below; enforced by
        # tests/quality/test_console_subprocess_encoding.py.
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            encoding=console_encoding(),
            errors="replace",
            timeout=20,
        )
        if r.returncode != 0:
            out["problems"].append("not installed")
            out["installed"] = False
            return out
        data = json.loads((r.stdout or "{}").strip() or "{}")
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"could not read the task ({exc})")
        return out

    out["installed"] = True
    out["last_result"] = data.get("last")
    out["next_run"] = data.get("next") or None
    # `enabled` is absent only from an older/partial reply; treat that as "assume on"
    # rather than inventing a failure, since every other field still means something.
    out["enabled"] = bool(data.get("enabled", True))
    # It can only bring a dead daemon back if it is ENABLED and still going to fire.
    # Leaving `enabled` out of this is what let a switched-off task read as Healthy.
    out["can_recover"] = (
        out["enabled"] and bool(out["next_run"]) and int(data.get("reps") or 0) > 0
    )
    if not out["enabled"]:
        # First, because it outranks the rest: a disabled task will not fire at all,
        # so its NextRunTime and repetition are describing something that cannot happen.
        out["problems"].append(
            "DISABLED — it will not fire, so it cannot start or recover the daemon "
            "(re-enable with: navig service start)"
        )
    if out["last_result"] not in _TASK_RESULT_NOT_A_FAILURE:
        out["problems"].append(f"last run FAILED (result {out['last_result']})")
    if not out["next_run"]:
        out["problems"].append("no next run scheduled — it cannot restart a dead daemon")
    elif not out["can_recover"] and out["enabled"]:
        out["problems"].append("no repeating trigger — it only fires once")
    return out


def _task_xml_enabled(xml_text: str) -> bool | None:
    """Read ``<Settings><Enabled>`` out of a Task Scheduler task definition.

    Returns ``None`` when the document cannot be parsed or carries no such element
    -- "I could not look", which the caller must NOT render as healthy.

    Deliberately scoped to the ``Settings`` element: each ``<Trigger>`` carries its
    OWN ``<Enabled>``, so a naive "first Enabled element" read reports a trigger's
    state as the task's.
    """
    from xml.etree import ElementTree

    try:
        # S314 (prefer defusedxml): the input is the stdout of schtasks.exe, a local
        # Windows system binary -- not network or user data. XXE does not apply:
        # xml.etree resolves no external entities and raises on undefined ones.
        # chr(0xFEFF) strips the BOM schtasks emits with its UTF-16 declaration.
        root = ElementTree.fromstring(xml_text.lstrip(chr(0xFEFF)))  # noqa: S314
    except ElementTree.ParseError:
        return None
    for element in root.iter():
        # Tags arrive namespace-qualified ({...}Settings); compare on the local name.
        if element.tag.rsplit("}", 1)[-1] != "Settings":
            continue
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == "Enabled":
                return (child.text or "").strip().lower() == "true"
        # A <Settings> block with NO <Enabled> child means ENABLED. Windows omits the
        # element when it holds the schema default (true) and writes it out only to
        # say `false` -- verified against the operator's real task, which carried
        # `<Enabled>false</Enabled>` while disabled and dropped the element entirely
        # once re-enabled. Treating "absent" as unreadable would report every HEALTHY
        # install as "state could not be read", i.e. swap one dishonest answer for
        # another.
        return True
    return None


def task_scheduler_enabled_state() -> tuple[bool | None, bool | None, str]:
    """Tri-state read of the autostart task: ``(enabled, installed, detail)``.

    ``enabled`` is ``True``/``False``, or ``None`` when the answer could not be
    established (not installed, or the definition could not be parsed).
    ``installed`` is ``False`` when the task does not exist and ``None`` when even
    that could not be determined.

    Split out of ``task_scheduler_status`` because a caller that wants to REPAIR a
    disabled task must be able to tell "disabled" from "absent" and from "I could
    not look" -- collapsing all three into one ``False`` is how a repair path ends
    up warning about a task the operator never installed. Matching on the human
    message to recover that distinction would be a locale bug waiting to happen.
    """
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/xml", "ONE"],
            capture_output=True,
            encoding=console_encoding(),
            errors="replace",
        )
        if result.returncode != 0:
            # NB: _summary_line is nested inside the status renderer, not importable here.
            raw = (result.stderr or result.stdout or "").strip()
            detail = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
            return None, False, detail or "not installed - install it with: navig service install"

        enabled = _task_xml_enabled(result.stdout or "")
        if enabled is None:
            # Could-not-verify is NOT healthy. Say so rather than showing Active.
            return None, True, "installed, but its enabled-state could not be read"
        if not enabled:
            return False, True, (
                "installed but DISABLED - it will not start the daemon at logon. "
                "Re-enable it with: navig service start"
            )
        return True, True, "enabled - starts the daemon at logon"
    except Exception as e:  # noqa: BLE001 - a status probe must never raise
        return None, None, f"could not query Task Scheduler: {e}"


def task_scheduler_status() -> tuple[bool, str]:
    """Report whether the autostart task will actually fire.

    Returns ``(enabled, detail)``; ``enabled`` is what the status line renders as
    Active / Inactive.

    **This used to be ``running = "running" in stdout.lower()``** over
    ``schtasks /query /v``. That output contains the FIELD LABEL
    ``Repeat: Stop If Still Running:`` for every task ever queried, so the check was
    unconditionally True: ``navig service status`` printed "Task Scheduler: Active"
    for a task whose own ``Status:`` field read ``Disabled``. Measured on the
    operator's machine 2026-09-04 -- their daemon had no autostart at all, the task
    had last exited 1, and the status command said everything was fine. A green light
    over an unknown is worse than a red one: it tells you not to look.

    The unit test that covered it fed ``stdout="Status: Running"`` -- a synthetic
    string that real ``schtasks`` never emits -- so the fake agreed with the bug.

    Reads the task XML rather than the human-readable dump because field labels AND
    their values are localised by Windows, while the XML schema's element names are
    not: a substring check for "Disabled" would pass on an English box and silently
    fail everywhere else.
    """
    enabled, _installed, detail = task_scheduler_enabled_state()
    # Anything but a definite True is NOT healthy -- "could not look" included.
    return enabled is True, detail


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
    home = str(_navig_home())
    log_path = str(_log_dir() / "daemon.log")

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
Environment=NAVIG_CONFIG_DIR={home}
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
            # "Active" used to mean only "the task exists", which is how a task
            # whose last run FAILED and which had no next run still printed
            # green while the daemon was dead. Report what it can actually do.
            health = task_scheduler_health()
            if health.get("installed") is False:
                lines.append("Task Scheduler: Not installed")
                lines.append("  Fix: navig service install")
            elif health.get("installed") is None:
                lines.append("Task Scheduler: UNKNOWN (could not read the task)")
                for problem in health.get("problems", []):
                    lines.append(f"  ! {problem}")
            elif health.get("problems"):
                lines.append("Task Scheduler: Installed but NOT healthy")
                for problem in health["problems"]:
                    lines.append(f"  ! {problem}")
                lines.append("  Fix: navig service install   (re-registers with a watchdog trigger)")
            else:
                nxt = health.get("next_run") or "?"
                lines.append(f"Task Scheduler: Healthy (watchdog re-checks, next {nxt})")
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
