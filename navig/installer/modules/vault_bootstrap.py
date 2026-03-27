"""
Installer module: ensure the vault is initialised.

Creates the vault key file if it does not already exist so subsequent
commands can use vault.put / vault.get without extra setup.

This module is intentionally minimal — it does NOT prompt for a master
password or write any credentials.  Telegram / AI-provider token storage
is handled by the onboarding wizard (navig/onboarding/steps.py).
"""

from __future__ import annotations

from typing import List

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "vault_bootstrap"
description = "Initialise vault (create key file if absent)"


def plan(ctx: InstallerContext) -> List[Action]:
    return [
        Action(
            id="vault_bootstrap.init",
            description="Ensure vault key file exists",
            module=name,
            reversible=False,
        )
    ]


def apply(action: Action, ctx: InstallerContext) -> Result:
    try:
        from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]

        vault = get_vault_v2()
        # Calling get_vault_v2() is enough to trigger key-file creation
        return Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            message="Vault initialised",
        )
    except ImportError:
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message="Vault module not available — skipped",
        )
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: a vault failure should not abort the whole install
        return Result(
            action_id=action.id,
            state=ModuleState.SKIPPED,
            message=f"Vault init skipped: {exc}",
        )
