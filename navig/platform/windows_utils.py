"""Windows-specific platform utilities.

All public functions guard against non-Windows platforms and raise RuntimeError
when called on Linux/macOS, unless documented otherwise.

Ported and hardened from CursorTouch/Windows-MCP (MIT licence) desktop/utils.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

try:
    import psutil  # type: ignore[import]
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

# ─── Unicode Private-Use Area ranges that cause rendering glitches ────────────
# Compiled once; strips BMP PUA (U+E000–U+F8FF) and both supplementary PUA
# planes (U+F0000–U+FFFFD, U+100000–U+10FFFD).
_PRIVATE_USE_AREA_PATTERN: re.Pattern = re.compile(
    "[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]"
)


# ─── PowerShell quoting ────────────────────────────────────────────────────────


def ps_quote(value: str) -> str:
    """Wrap *value* in a PowerShell single-quoted string, escaping embedded ``'``."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def ps_quote_for_xml(value: str) -> str:
    """XML-escape *value* then wrap it in a PowerShell single-quoted string.

    Use this when the value will be embedded inside XML/HTML that is itself
    passed as an argument to a PowerShell command (e.g. toast notification XML).
    """
    xml_escaped = (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
    return ps_quote(xml_escaped)


# ─── Unicode cleanup ───────────────────────────────────────────────────────────


def remove_private_use_chars(text: str) -> str:
    """Strip all Unicode Private-Use Area characters from *text*.

    Applications such as VS Code embed PUA characters in UIAutomation element
    names (e.g. navigation bar items). Leaving them in causes rendering issues
    in terminals and MCP responses.
    """
    return _PRIVATE_USE_AREA_PATTERN.sub("", text)


# ─── Process helpers ───────────────────────────────────────────────────────────


def check_pid_exists(pid: int) -> bool:
    """Return *True* if a process with *pid* exists and is not a zombie/dead.

    Requires ``psutil``; returns *False* if psutil is unavailable.
    """
    psutil_mod = sys.modules.get("psutil", psutil)
    if psutil_mod is None:
        return False
    try:
        proc = psutil_mod.Process(pid)
        zombie_status = getattr(psutil_mod, "STATUS_ZOMBIE", "zombie")
        dead_status = getattr(psutil_mod, "STATUS_DEAD", "dead")
        return proc.status() not in (zombie_status, dead_status)
    except getattr(psutil_mod, "NoSuchProcess", Exception):
        return False


# ─── Graceful subprocess timeout ──────────────────────────────────────────────

# How long (seconds) to wait after CTRL_BREAK before resorting to taskkill.
_GRACEFUL_KILL_GRACE_PERIOD: float = 2.0


def run_with_graceful_timeout(
    *popenargs,
    timeout: float,
    grace_period: float = _GRACEFUL_KILL_GRACE_PERIOD,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Drop-in replacement for ``subprocess.run`` with Windows-aware timeout.

    On timeout the function attempts a two-stage shutdown:

    1. Sends ``CTRL_BREAK_EVENT`` to the process group (allows ``pwsh`` to
       flush its output buffer) and waits *grace_period* seconds.
    2. If the process is still alive, runs ``taskkill /PID <pid> /T /F`` to
       kill the entire process tree (handles nested ``python`` sub-processes).

    This reliably terminates ``pwsh → python`` scenarios that hang on a plain
    ``.terminate()`` call (fixes issues analogous to Windows-MCP #124, #146).

    Raises ``subprocess.TimeoutExpired`` after killing the process, preserving
    the same contract as the standard ``subprocess.run``.
    """
    if sys.platform != "win32":
        return subprocess.run(*popenargs, timeout=timeout, **kwargs)

    # Convert capture_output shorthand (subprocess.run-only) to explicit PIPE.
    if kwargs.pop("capture_output", False):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)

    # CREATE_NEW_PROCESS_GROUP is required so we can send CTRL_BREAK_EVENT.
    creation_flags = kwargs.pop("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP

    import signal  # noqa: PLC0415

    with subprocess.Popen(
        *popenargs,
        creationflags=creation_flags,
        **kwargs,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            try:
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            except (OSError, AttributeError):
                pass
            try:
                proc.wait(timeout=grace_period)
            except subprocess.TimeoutExpired:
                # Nuclear option: kill full process tree.
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True,
                    timeout=5,
                )
            finally:
                proc.kill()
            stdout_data = b""
            stderr_data = b""
            try:
                stdout_data, stderr_data = proc.communicate(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            raise subprocess.TimeoutExpired(  # noqa: B904
                proc.args, timeout, output=stdout_data, stderr=stderr_data
            ) from None
