"""
Installer module: create the ~/.navig directory tree.

Creates the canonical subdirectory structure so all subsequent modules
can assume the layout exists.
"""

from __future__ import annotations

from pathlib import Path

from navig.installer.contracts import Action, InstallerContext, ModuleState, Result

name = "config_paths"
description = "Create ~/.navig directory structure"

# Subdirectories that must always exist under config_dir
_SUBDIRS = [
    "",  # config_dir itself
    "workspace",
    "logs",
    "cache",
    "history",
    "hosts",
    "apps",
]


def plan(ctx: InstallerContext) -> list[Action]:
    """Emit one Action per missing directory."""
    actions: list[Action] = []
    for sub in _SUBDIRS:
        d: Path = ctx.config_dir / sub if sub else ctx.config_dir
        if not d.exists():
            actions.append(
                Action(
                    id=f"config_paths.mkdir.{sub or 'root'}",
                    description=f"Create {d}",
                    module=name,
                    data={"path": str(d), "existed": False},
                    reversible=True,
                )
            )
    return actions


def apply(action: Action, ctx: InstallerContext) -> Result:
    p = Path(action.data["path"])
    p.mkdir(parents=True, exist_ok=True)
    return Result(
        action_id=action.id,
        state=ModuleState.APPLIED,
        message=f"Created {p}",
        undo_data={"path": str(p), "existed": action.data.get("existed", False)},
    )


def rollback(action: Action, result: Result, ctx: InstallerContext) -> None:
    """Remove directories that were created fresh (never removes non-empty dirs)."""
    if result.undo_data.get("existed"):
        return
    p = Path(result.undo_data["path"])
    try:
        p.rmdir()  # only succeeds if empty
    except OSError:
        pass  # best-effort
