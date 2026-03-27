"""
Installer runner: apply a list of Actions and collect Results.

Usage::

    from navig.installer.runner import apply, rollback
    from navig.installer.contracts import InstallerContext

    ctx  = InstallerContext(profile="operator", dry_run=False)
    results = apply(actions, ctx)
    # On failure:
    rollback(actions, results, ctx)
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from navig.installer.contracts import Action, InstallerContext, Result

from navig.installer.contracts import ModuleState, Result


def apply(actions: "List[Action]", ctx: "InstallerContext") -> "List[Result]":
    """Apply *actions* sequentially; stop on the first FAILED result.

    In dry_run mode every action is wrapped in a SKIPPED result without
    calling any module logic.
    """
    results: List[Result] = []

    for action in actions:
        # --- dry-run fast path ---
        if ctx.dry_run:
            results.append(
                Result(
                    action_id=action.id,
                    state=ModuleState.SKIPPED,
                    message=f"[dry-run] {action.description}",
                )
            )
            continue

        # --- placeholder skip ---
        if action.data.get("placeholder"):
            results.append(
                Result(
                    action_id=action.id,
                    state=ModuleState.SKIPPED,
                    message=action.description,
                )
            )
            continue

        # --- real apply ---
        try:
            mod = importlib.import_module(f"navig.installer.modules.{action.module}")
            result: Result = mod.apply(action, ctx)
        except Exception as exc:  # noqa: BLE001
            result = Result(
                action_id=action.id,
                state=ModuleState.FAILED,
                message=action.description,
                error=str(exc),
            )

        results.append(result)

        if result.state == ModuleState.FAILED:
            break  # halt on first hard failure

    return results


def rollback(
    actions: "List[Action]",
    results: "List[Result]",
    ctx: "InstallerContext",
) -> None:
    """Roll back applied actions in reverse order (best-effort)."""
    applied_pairs = [
        (a, r)
        for a, r in zip(actions, results)
        if r.state == ModuleState.APPLIED and a.reversible
    ]

    for action, result in reversed(applied_pairs):
        try:
            mod = importlib.import_module(f"navig.installer.modules.{action.module}")
            if hasattr(mod, "rollback"):
                mod.rollback(action, result, ctx)
        except Exception:  # noqa: BLE001
            pass  # best-effort; never raise during rollback
