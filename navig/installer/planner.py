"""
Installer planner: resolve a profile into an ordered list of Actions.

Usage::

    from navig.installer.planner import plan
    from navig.installer.contracts import InstallerContext

    ctx = InstallerContext(profile="operator")
    actions = plan(ctx)
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.installer.contracts import Action, InstallerContext

from navig.installer.profiles import PROFILE_MODULES, VALID_PROFILES


def _load_module(name: str):
    """Dynamically import navig.installer.modules.<name>."""
    return importlib.import_module(f"navig.installer.modules.{name}")


def plan(ctx: InstallerContext) -> list[Action]:
    """Return actions for *ctx.profile* in the correct apply order.

    Raises
    ------
    ValueError
        If *ctx.profile* is not a known profile name.
    """
    if ctx.profile not in VALID_PROFILES:
        raise ValueError(
            f"Unknown installer profile: {ctx.profile!r}. "
            f"Valid profiles: {', '.join(VALID_PROFILES)}"
        )

    actions: list[Action] = []
    for mod_name in PROFILE_MODULES[ctx.profile]:
        try:
            mod = _load_module(mod_name)
        except ModuleNotFoundError:
            # Module not yet implemented — emit a placeholder skip action
            from navig.installer.contracts import Action

            actions.append(
                Action(
                    id=f"{mod_name}.placeholder",
                    description=f"[placeholder] {mod_name} not yet implemented",
                    module=mod_name,
                    data={"placeholder": True},
                    reversible=False,
                )
            )
            continue

        module_actions = mod.plan(ctx)
        actions.extend(module_actions)

    return actions
