"""Installer module: NAVIG daemon service registration.

Delegates entirely to ``navig.daemon.service_manager``:
- On Windows: nssm → task-scheduler fallback
- On Linux: systemd unit
- On macOS: not yet supported (SKIPPED)

Included in: system_standard, system_deep profiles.
"""

from __future__ import annotations

import sys
from typing import List

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "service"
description = "Register NAVIG daemon as a system service"


# ── helpers ──────────────────────────────────────────────────────────────────


def _is_supported() -> bool:
    return sys.platform in ("win32", "linux")


def _service_installed() -> bool:
    """Best-effort check — True if the service appears registered."""
    try:
        if sys.platform == "win32":
            import subprocess

            r = subprocess.run(
                ["sc", "query", "NavigDaemon"],
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0
        elif sys.platform == "linux":
            import subprocess

            r = subprocess.run(
                ["systemctl", "is-enabled", "navig"],
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0
    except Exception:  # noqa: BLE001
        pass
    return False


# ── module API ────────────────────────────────────────────────────────────────


def plan(ctx: InstallerContext) -> List[Action]:
    if not _is_supported():
        return []
    if _service_installed():
        return []
    return [
        Action(
            id="service.install",
            description="service: register NAVIG daemon as system service",
            module=name,
            data={"platform": sys.platform},
            reversible=True,
        )
    ]


def apply(action: Action, ctx: InstallerContext) -> Result:
    if not _is_supported():
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message=f"unsupported platform: {sys.platform}",
        )

    try:
        from navig.daemon import service_manager  # type: ignore[import]
    except ImportError as exc:
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message=f"service_manager unavailable: {exc}",
        )

    try:
        ok, msg = service_manager.install(start_now=False)
        if ok:
            return Result(
                action_id=action.id,
                state=ModuleState.APPLIED,
                message=msg,
                undo_data={"platform": sys.platform},
            )
        return Result(
            action_id=action.id,
            state=ModuleState.FAILED,
            message=msg,
        )
    except Exception as exc:  # noqa: BLE001
        return Result(
            action_id=action.id,
            state=ModuleState.FAILED,
            message=str(exc),
        )


def rollback(action: Action, result: Result, ctx: InstallerContext) -> None:
    """Unregister the service via service_manager.uninstall()."""
    try:
        from navig.daemon import service_manager  # type: ignore[import]

        service_manager.uninstall()
    except Exception:  # noqa: BLE001
        pass
