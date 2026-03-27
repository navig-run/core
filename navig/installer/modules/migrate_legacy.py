"""
Installer module: migrate legacy config paths to ~/.navig.

Delegates to the existing helpers in navig/commands/init.py so there
is exactly one source of truth for migration logic.

Two migrations are attempted (both non-fatal if they fail):
  1. Windows platformdirs nested layout → canonical dirs
  2. Documents/.navig             → ~/.navig
"""

from __future__ import annotations

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "migrate_legacy"
description = "Migrate legacy config paths to ~/.navig"


def plan(ctx: InstallerContext) -> list[Action]:
    return [
        Action(
            id="migrate_legacy.run",
            description="Check & migrate legacy config paths",
            module=name,
            reversible=False,
        )
    ]


def apply(action: Action, ctx: InstallerContext) -> Result:
    messages: list[str] = []
    skipped: list[str] = []

    # --- Windows nested platformdirs layout ---
    try:
        from navig.commands.init import _migrate_legacy_windows_runtime_layout

        _migrate_legacy_windows_runtime_layout()
        messages.append("Windows runtime layout")
    except Exception as exc:  # noqa: BLE001
        skipped.append(f"Windows layout: {exc}")

    # --- Documents/.navig → ~/.navig ---
    try:
        from navig.commands.init import _migrate_legacy_documents_dir

        _migrate_legacy_documents_dir(ctx.config_dir)
        messages.append("Documents/.navig")
    except Exception as exc:  # noqa: BLE001
        skipped.append(f"Documents migration: {exc}")

    if messages:
        return Result(
            action_id=action.id,
            state=ModuleState.APPLIED,
            message="Migrated: " + ", ".join(messages),
        )

    # Nothing to migrate is a normal / expected case
    return Result(
        action_id=action.id,
        state=ModuleState.SKIPPED,
        message="No legacy paths to migrate" + (f" ({'; '.join(skipped)})" if skipped else ""),
    )
