"""Windows PowerShell executor for local automation tasks.

Provides a reliable ``PowerShellExecutor`` that:
- Auto-detects ``pwsh`` (PowerShell 7+) vs ``powershell`` (Windows PS 5.1).
- Encodes every command via ``-EncodedCommand`` (avoids all quoting issues).
- Forces UTF-8 output via a prepended ``$OutputEncoding`` prefix.
- Builds a complete child-process environment from registry PATH/PATHEXT
  entries so that commands work even when launched from a restricted MCP
  stdio environment.
- Delegates graceful timeout handling to
  ``navig.platform.windows_utils.run_with_graceful_timeout``.

Ported and hardened from CursorTouch/Windows-MCP (MIT licence)
desktop/powershell.py.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from functools import lru_cache
from typing import NamedTuple

from navig.platform.windows_utils import run_with_graceful_timeout

# ─── Constants ────────────────────────────────────────────────────────────────

# Ordered by preference: try pwsh (PS 7+) first, fall back to legacy PS 5.1.
_POWERSHELL_EXECUTABLES: tuple[str, ...] = ("pwsh", "powershell")

# Prepended to every command to guarantee UTF-8 stdout/stderr in both PS
# versions.  [Text.UTF8Encoding]::new() is available in PS 5.1+.
_ENCODING_PREFIX: str = (
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
    "$OutputEncoding = [Text.UTF8Encoding]::new(); "
)

# Registry key that holds the system-level environment variables.
_REG_SYSTEM_ENV = (
    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
)
_REG_USER_ENV = r"Environment"


# ─── Environment builder ──────────────────────────────────────────────────────


def _expand_reg_sz(value: str, existing_env: dict[str, str]) -> str:
    """Expand ``%VAR%`` placeholders in a REG_EXPAND_SZ value."""
    return os.path.expandvars(value)


def _read_reg_env(hive, subkey: str) -> dict[str, str]:
    """Read all string values from a registry environment key.

    Returns an empty dict if the key is inaccessible (e.g. non-Windows or
    insufficient permissions).
    """
    if sys.platform != "win32":
        return {}
    try:
        import winreg  # type: ignore[import]  # noqa: PLC0415

        result: dict[str, str] = {}
        with winreg.OpenKey(hive, subkey) as key:
            i = 0
            while True:
                try:
                    name, data, reg_type = winreg.EnumValue(key, i)
                    i += 1
                    import winreg as _wr  # noqa: PLC0415

                    if reg_type in (
                        _wr.REG_SZ,
                        _wr.REG_EXPAND_SZ,
                    ) and isinstance(data, str):
                        result[name.upper()] = data
                except OSError:
                    break
        return result
    except Exception:  # noqa: BLE001
        return {}


@lru_cache(maxsize=1)
def _build_child_env() -> dict[str, str]:
    """Build a complete environment block for PowerShell child processes.

    Supplements ``os.environ`` with values from the system and user registry
    environment keys.  PATH entries are deduplicated while preserving order.
    Calling this function is cheap after the first call (lru_cache).
    """
    if sys.platform != "win32":
        return dict(os.environ)

    try:
        import winreg  # type: ignore[import]  # noqa: PLC0415

        sys_env = _read_reg_env(winreg.HKEY_LOCAL_MACHINE, _REG_SYSTEM_ENV)
        usr_env = _read_reg_env(winreg.HKEY_CURRENT_USER, _REG_USER_ENV)
    except ImportError:
        sys_env = {}
        usr_env = {}

    env = dict(os.environ)

    # Merge registry values; do NOT overwrite values already inherited.
    for name, value in {**sys_env, **usr_env}.items():
        if name not in env:
            env[name] = value

    # Deduplicate PATH while preserving order.
    raw_path = env.get("PATH", "")
    seen: dict[str, None] = {}
    for entry in raw_path.split(os.pathsep):
        entry = entry.strip()
        if entry:
            seen[entry.lower()] = None  # case-insensitive dedup key
    # Rebuild with original-case first occurrence.
    deduped_entries: list[str] = []
    lowered_seen: set[str] = set()
    for entry in raw_path.split(os.pathsep):
        entry = entry.strip()
        if entry and entry.lower() not in lowered_seen:
            deduped_entries.append(entry)
            lowered_seen.add(entry.lower())
    env["PATH"] = os.pathsep.join(deduped_entries)

    # Ensure PATHEXT has a fallback.
    if "PATHEXT" not in env:
        env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"

    # Suppress ANSI color codes so callers receive clean text.
    env["NO_COLOR"] = "1"

    return env


# ─── Executable detection ─────────────────────────────────────────────────────


class _PSInfo(NamedTuple):
    executable: str
    is_core: bool  # True when executable is pwsh (PS 7+)


@lru_cache(maxsize=1)
def _detect_powershell() -> _PSInfo:
    """Detect the best available PowerShell executable once and cache it."""
    import shutil  # noqa: PLC0415

    for exe in _POWERSHELL_EXECUTABLES:
        found = shutil.which(exe)
        if found:
            return _PSInfo(executable=found, is_core=(exe == "pwsh"))
    # Fallback: assume 'powershell' is on PATH (Windows built-in).
    return _PSInfo(executable="powershell", is_core=False)


# ─── PowerShellExecutor ────────────────────────────────────────────────────────


class PowerShellExecutor:
    """Execute PowerShell commands reliably on the local Windows machine.

    Usage::

        result = PowerShellExecutor.execute_command("Get-Date", timeout=10)
        print(result.stdout)
    """

    @staticmethod
    def execute_command(
        command: str,
        timeout: float = 30.0,
        shell: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Run *command* in PowerShell and return a ``CompletedProcess``.

        Args:
            command:  The PowerShell command/script text to execute.
            timeout:  Per-execution wall-clock timeout in seconds.
            shell:    Override executable path.  If *None*, auto-detected.

        Returns:
            ``subprocess.CompletedProcess`` with decoded ``stdout``/``stderr``
            strings (UTF-8, errors replaced).

        Raises:
            RuntimeError: When called on a non-Windows platform.
            subprocess.TimeoutExpired: When the command exceeds *timeout*.
        """
        if sys.platform != "win32":
            raise RuntimeError("PowerShellExecutor is Windows-only")

        ps_info = _detect_powershell()
        executable = shell or ps_info.executable

        full_command = _ENCODING_PREFIX + command
        encoded = base64.b64encode(full_command.encode("utf-16-le")).decode("ascii")

        args: list[str] = [executable, "-NonInteractive", "-NoProfile"]
        if not ps_info.is_core:
            # PS 5.1 needs -OutputFormat Text to suppress XML serialisation.
            args += ["-OutputFormat", "Text"]
        args += ["-EncodedCommand", encoded]

        proc = run_with_graceful_timeout(
            args,
            timeout=timeout,
            capture_output=True,
            env=_build_child_env(),
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        # Decode bytes → str; replace undecodable bytes rather than crashing.
        if isinstance(proc.stdout, bytes):
            proc = subprocess.CompletedProcess(
                proc.args,
                proc.returncode,
                proc.stdout.decode("utf-8", errors="replace"),
                proc.stderr.decode("utf-8", errors="replace")
                if isinstance(proc.stderr, bytes)
                else (proc.stderr or ""),
            )
        return proc
