"""Installer module: NAVIG daemon service registration.

Delegates entirely to ``navig.daemon.service_manager``:
- On Windows: nssm → task-scheduler fallback
- On Linux: systemd unit
- On macOS: a per-user LaunchAgent (launchd)

Included in: system_standard, system_deep profiles.
"""

from __future__ import annotations

import sys

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "service"
description = "Register NAVIG daemon as a system service"

# Seconds allowed for sc/systemctl probe subprocess calls.
_SERVICE_CMD_TIMEOUT: int = 5


# ── helpers ──────────────────────────────────────────────────────────────────


def _is_supported() -> bool:
    return sys.platform in ("win32", "linux", "darwin")


def _service_installed() -> bool:
    """True if the daemon's service is registered — asked of the code that registers it.

    This probed names nothing creates: ``sc query NavigDaemon`` (the default Windows
    method is the "NAVIG Daemon" scheduled task, not an nssm service) and
    ``systemctl is-enabled navig`` (the unit is ``navig-agent``, in the USER scope).
    So on Linux it always read "not installed" and re-planned an install every run.
    """
    try:
        from navig.daemon.launch import service_is_installed

        if service_is_installed():
            return True
        if sys.platform == "win32":  # an nssm-method install is a real service
            import subprocess

            from navig.daemon.service_manager import SERVICE_NAME

            r = subprocess.run(
                ["sc", "query", SERVICE_NAME],
                capture_output=True,
                timeout=_SERVICE_CMD_TIMEOUT,
            )
            return r.returncode == 0
    except Exception:  # noqa: BLE001
        pass
    return False


# ── module API ────────────────────────────────────────────────────────────────


def plan(ctx: InstallerContext) -> list[Action]:
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
