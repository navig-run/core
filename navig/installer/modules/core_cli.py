"""
Installer module: verify the navig CLI binary is reachable.

This is always a prerequisite check, not a write operation.
It is non-reversible (nothing to undo).
"""

from __future__ import annotations

import shutil
import sys
from typing import List

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "core_cli"
description = "Verify navig CLI is on PATH"


def plan(ctx: InstallerContext) -> List[Action]:
    return [
        Action(
            id="core_cli.verify",
            description="Verify navig CLI is reachable",
            module=name,
            reversible=False,
        )
    ]


def apply(action: Action, ctx: InstallerContext) -> Result:
    found = shutil.which("navig")
    if found:
        ver = _navig_version()
        return Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            message=f"navig {ver} found at {found}",
        )

    # Fallback: python -m navig
    try:
        import subprocess

        r = subprocess.run(
            [sys.executable, "-m", "navig", "--version"],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0:
            return Result(
                action_id=action.id,
                state=ModuleState.APPLIED,
                message="navig available via python -m navig",
            )
    except Exception:  # noqa: BLE001
        pass

    return Result(
        action_id=action.id,
        state=ModuleState.FAILED,
        error="navig not found on PATH — install with: pip install navig",
    )


# ─────────────────────── helpers ──────────────────────────────────────────────

def _navig_version() -> str:
    try:
        import importlib.metadata

        return importlib.metadata.version("navig")
    except Exception:  # noqa: BLE001
        return "unknown"
