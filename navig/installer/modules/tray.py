"""Installer module: Windows system-tray integration.

Windows-only.  Delegates to the tray install PowerShell script via
``navig.commands.tray.INSTALL_SCRIPT``.  Non-Windows platforms receive
a SKIPPED result — no actions are planned.

Included in: system_deep profile.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "tray"
description = "Install NAVIG system-tray app (Windows only)"


# ── helpers ──────────────────────────────────────────────────────────────────


def _install_script() -> Path | None:
    """Return path to tray install.ps1 from commands.tray constants."""
    try:
        from navig.commands import tray as tray_mod  # type: ignore[import]

        script = getattr(tray_mod, "INSTALL_SCRIPT", None)
        if script and Path(script).exists():
            return Path(script)
    except Exception:  # noqa: BLE001
        pass
    return None


def _desktop_shortcut_exists() -> bool:
    if sys.platform != "win32":
        return False
    import os

    shortcut = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "NAVIG Tray.lnk"
    return shortcut.exists()


# ── module API ────────────────────────────────────────────────────────────────


def plan(ctx: InstallerContext) -> List[Action]:
    if sys.platform != "win32":
        return []
    if _desktop_shortcut_exists():
        return []
    return [
        Action(
            id="tray.install",
            description="tray: install NAVIG Tray (desktop shortcut)",
            module=name,
            data={"platform": "win32"},
            reversible=True,
        )
    ]


def apply(action: Action, ctx: InstallerContext) -> Result:
    if sys.platform != "win32":
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message="tray install is Windows-only",
        )

    install_script = _install_script()
    if install_script is None:
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message="tray install script not found",
        )

    try:
        subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(install_script),
                "-Python",
                sys.executable,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            message="tray installed",
            undo_data={"platform": "win32"},
        )
    except subprocess.CalledProcessError as exc:
        return Result(
            action_id=action.id,
            state=ModuleState.FAILED,
            message=f"tray install script failed: {exc.returncode}",
        )
    except Exception as exc:  # noqa: BLE001
        return Result(
            action_id=action.id,
            state=ModuleState.FAILED,
            message=str(exc),
        )


def rollback(action: Action, result: Result, ctx: InstallerContext) -> None:
    """Delegate tray removal to navig.commands.tray.tray_uninstall() logic."""
    if sys.platform != "win32":
        return
    try:
        # Remove auto-start registry entry
        import winreg  # type: ignore[import]

        key = winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_WRITE,  # type: ignore[attr-defined]
        )
        try:
            winreg.DeleteValue(key, "NavigTray")  # type: ignore[attr-defined]
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    # Remove desktop shortcut
    import os

    shortcut = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "NAVIG Tray.lnk"
    try:
        shortcut.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
