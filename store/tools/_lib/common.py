"""
Shared helpers for all NAVIG tool scripts.
Provides: JSON envelope, USB root resolver, platform detection, subprocess runner.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Platform ─────────────────────────────────────────────────────────────────


def current_os() -> str:
    s = platform.system().lower()
    return "mac" if s == "darwin" else ("windows" if s == "windows" else "linux")


def is_windows() -> bool:
    return current_os() == "windows"


def is_admin() -> bool:
    try:
        if is_windows():
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


# ── USB root resolution ───────────────────────────────────────────────────────

_USB_ENV = "NAVIG_USB_ROOT"
_FIXED_WIN = [Path("C:/USB")]
_FIXED_UNIX = [Path("/mnt/usb"), Path("/media/usb")]


def find_usb_root() -> Path | None:
    env = os.environ.get(_USB_ENV)
    if env and Path(env).exists():
        return Path(env)
    for p in _FIXED_WIN if is_windows() else _FIXED_UNIX:
        if p.exists():
            return p
    if is_windows():
        try:
            import ctypes

            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                if bitmask & 1:
                    drive = Path(f"{letter}:\\")
                    if (
                        ctypes.windll.kernel32.GetDriveTypeW(str(drive)) == 2
                        and drive.exists()
                    ):
                        return drive
                bitmask >>= 1
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
    return None


def require_usb_root() -> Path:
    r = find_usb_root()
    if not r:
        raise RuntimeError(
            "USB root not found. Connect the USB drive or set NAVIG_USB_ROOT."
        )
    return r


# ── Exe resolver ─────────────────────────────────────────────────────────────

_TOOL_EXES: dict[str, dict[str, str | None]] = {
    "iperf3": {"windows": "network/iperf3/iperf3.exe", "linux": None, "mac": None},
    "rclone": {
        "windows": "network/rclone-browser/rclone/rclone.exe",
        "linux": "network/rclone-browser/rclone/rclone",
        "mac": "network/rclone-browser/rclone/rclone",
    },
    "nssm": {"windows": "system/nssm/win64/nssm.exe", "linux": None, "mac": None},
    "vivetool": {"windows": "system/vivetool/ViVeTool.exe", "linux": None, "mac": None},
    "procmon": {"windows": "system/procmon/Procmon64.exe", "linux": None, "mac": None},
    "nvidia_check": {
        "windows": "system/nvidia-updater/TinyNvidiaUpdateChecker.exe",
        "linux": None,
        "mac": None,
    },
    "futurerestore": {
        "windows": "ios/futurerestore/futurerestore.exe",
        "linux": None,
        "mac": None,
    },
}

# System tools (not USB-resident) — resolved from PATH
_SYSTEM_TOOLS = {
    "rclone_sys",
    "iperf3_sys",
    "yt-dlp",
    "gh",
    "scrot",
    "screencapture",
    "gnome-screenshot",
}


def resolve_usb_exe(tool_key: str) -> Path:
    os_key = current_os()
    rel = (_TOOL_EXES.get(tool_key) or {}).get(os_key)
    if not rel:
        raise RuntimeError(f"Tool '{tool_key}' not available on {os_key}.")
    exe = require_usb_root() / rel
    if not exe.exists():
        raise FileNotFoundError(f"Not found: {exe}  — ensure USB is connected.")
    return exe


def find_on_path(name: str) -> Path | None:
    import shutil

    found = shutil.which(name)
    return Path(found) if found else None


def require_on_path(name: str) -> Path:
    p = find_on_path(name)
    if not p:
        raise FileNotFoundError(f"'{name}' not found on PATH. Install it first.")
    return p


# ── JSON envelope ─────────────────────────────────────────────────────────────


def ok(
    tool_id: str,
    command: str,
    data: Any = None,
    warnings: list[str] | None = None,
    ms: int = 0,
    backend: str = "worker",
) -> dict:
    return {
        "ok": True,
        "tool": tool_id,
        "command": command,
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
        "warnings": warnings or [],
        "errors": [],
        "metrics": {"ms": ms, "backend": backend},
    }


def err(
    tool_id: str,
    command: str,
    errors: list[str],
    warnings: list[str] | None = None,
    ms: int = 0,
    backend: str = "worker",
) -> dict:
    return {
        "ok": False,
        "tool": tool_id,
        "command": command,
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {},
        "warnings": warnings or [],
        "errors": errors,
        "metrics": {"ms": ms, "backend": backend},
    }


def emit(envelope: dict) -> None:
    print(json.dumps(envelope, ensure_ascii=False), flush=True)


# ── Subprocess ────────────────────────────────────────────────────────────────


def run(
    args: list[str | Path],
    timeout: int = 60,
    dry_run: bool = False,
    env: dict | None = None,
) -> tuple[int, str, str]:
    """Run subprocess. Returns (returncode, stdout, stderr)."""
    if dry_run:
        return 0, f"[dry-run] {' '.join(str(a) for a in args)}", ""
    result = subprocess.run(
        [str(a) for a in args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ── Timing ────────────────────────────────────────────────────────────────────

import time


class Timer:
    def __init__(self):
        self._start = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)
