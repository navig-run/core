"""
navig.installer — idempotent profile-driven installer for NAVIG.

Public API
----------
run_install(profile, dry_run, quiet)
    Orchestrate a full install run: plan → apply → persist → report.

Profiles
--------
  node            minimal: config dirs + CLI verify + legacy migration
  operator        node + shell integration + vault bootstrap  ← default
  architect       operator + MCP pack
  system_standard operator + service daemon
  system_deep     system_standard + tray + persona assets

Examples
--------
CLI (added to `navig init`)::

    navig init --profile operator
    navig init --profile node --dry-run
    navig init --profile system_standard --quiet

Python::

    from navig.installer import run_install
    run_install(profile="operator", dry_run=True)
"""

from __future__ import annotations

from pathlib import Path

from navig.installer import state as _state
from navig.installer.contracts import InstallerContext, ModuleState, Result
from navig.installer.planner import plan
from navig.installer.profiles import DEFAULT_PROFILE, VALID_PROFILES
from navig.installer.runner import apply


def run_install(
    profile: str = DEFAULT_PROFILE,
    dry_run: bool = False,
    quiet: bool = False,
    config_dir: Path | None = None,
    extra: dict | None = None,
) -> list[Result]:
    """Run the installer for *profile* and return Results.

    Parameters
    ----------
    profile:
        One of VALID_PROFILES.
    dry_run:
        Preview actions without making any changes.
    quiet:
        Suppress console output (useful in CI / script installs).
    config_dir:
        Override the default ~/.navig base directory.
    extra:
        Arbitrary extra data forwarded to every module.

    Returns
    -------
    List[Result]
        One entry per planned action.

    Raises
    ------
    ValueError
        If *profile* is unknown.
    SystemExit
        If a critical module fails and quiet=False.
    """
    ctx = InstallerContext(
        profile=profile,
        dry_run=dry_run,
        quiet=quiet,
        config_dir=config_dir or Path.home() / ".navig",
        extra=extra or {},
    )

    actions = plan(ctx)

    if not quiet:
        _print_plan(actions, profile, dry_run)

    results = apply(actions, ctx)

    # Persist only when something was applied (not pure dry-run)
    applied = [r for r in results if r.state == ModuleState.APPLIED]
    if applied and not dry_run:
        try:
            manifest = _state.save(actions, results, ctx)
            if not quiet:
                from navig import console_helper as ch

                ch.dim(f"  manifest: {manifest}")
        except Exception:  # noqa: BLE001
            pass  # non-fatal

    if not quiet:
        _print_results(results, dry_run)

    return results


# ─────────────────────── private console helpers ──────────────────────────────


def _print_plan(actions, profile: str, dry_run: bool) -> None:
    try:
        from navig import console_helper as ch

        label = "[dry-run] " if dry_run else ""
        ch.header(f"{label}NAVIG Installer — profile: {profile}")
        for a in actions:
            ch.dim(f"  • {a.description}")
        ch.newline()
    except Exception:  # noqa: BLE001
        pass


def _print_results(results: list[Result], dry_run: bool) -> None:
    try:
        from navig import console_helper as ch

        for r in results:
            if r.state == ModuleState.APPLIED:
                ch.success(f"  ✓ {r.message}")
            elif r.state == ModuleState.SKIPPED:
                ch.dim(f"  · {r.message}")
            elif r.state == ModuleState.FAILED:
                ch.error(r.message, r.error or "")
        ch.newline()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "run_install",
    "DEFAULT_PROFILE",
    "VALID_PROFILES",
]
