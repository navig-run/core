"""
``navig migrate`` — ordered migration pipeline.

Steps
------
config     Migrate Documents\\.navig → ~/.navig config layout
           (wraps scripts/migrate_navig_config.py logic)
addons     Migrate legacy addons/ → templates/ architecture
all        Run all steps in dependency order (idempotent)
"""

from __future__ import annotations

import typer

from navig import console_helper as ch

migrate_app = typer.Typer(
    name="migrate",
    help="Run configuration and data migrations.",
    invoke_without_command=True,
    no_args_is_help=True,
)

# ── Ordered pipeline ─────────────────────────────────────────────────────────

MIGRATION_STEPS: list[tuple[str, str]] = [
    ("config",  "Migrate Documents/.navig → ~/.navig layout"),
    ("addons",  "Migrate addons/ → templates/ architecture"),
]


def _done_file():
    from navig.config import get_config_manager
    cm = get_config_manager()
    return cm.global_config_dir / ".migrations_done"


def _mark_done(name: str) -> None:
    path = _done_file()
    done = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    done.add(name)
    path.write_text("\n".join(sorted(done)) + "\n", encoding="utf-8")


def _is_done(name: str) -> bool:
    path = _done_file()
    if not path.exists():
        return False
    return name in path.read_text(encoding="utf-8").splitlines()


# ── migrate config ────────────────────────────────────────────────────────────

@migrate_app.command("config")
def migrate_config_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without saving."),
    force: bool = typer.Option(False, "--force", help="Re-run even if already marked complete."),
) -> None:
    """
    Migrate Documents\\.navig → ~/.navig config layout.

    Migrates all configuration files from the legacy Windows
    Documents\\.navig location to the canonical ~/.navig directory.
    Idempotent — safe to re-run.
    """
    step = "config"
    if _is_done(step) and not force:
        ch.success(f"Migration '{step}' already complete (use --force to re-run).")
        return

    # Pull in the logic from scripts/migrate_navig_config.py
    try:
        import sys
        from pathlib import Path
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from migrate_navig_config import migrate_config  # type: ignore[import]
        migrate_config(dry_run=dry_run, force=force)
    except ImportError:
        ch.warning("migrate_navig_config script not found — running built-in fallback.")
        _builtin_config_migration(dry_run=dry_run)

    if not dry_run:
        _mark_done(step)
        ch.success(f"Migration '{step}' complete.")


def _builtin_config_migration(dry_run: bool) -> None:
    """Lightweight built-in fallback if the scripts/ version is absent."""
    import shutil

    from navig.platform.paths import config_dir, legacy_documents_config_dir

    source = legacy_documents_config_dir()
    dest = config_dir()

    if not source.exists():
        ch.info(f"Source {source} does not exist — nothing to migrate.")
        return

    ch.info(f"Migrating {source} → {dest}")
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        target = dest / rel
        if dry_run:
            ch.dim(f"  would copy: {rel}")
            continue
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(item, target)
                ch.dim(f"  copied: {rel}")
            else:
                ch.dim(f"  skipped (exists): {rel}")


# ── migrate addons ────────────────────────────────────────────────────────────

@migrate_app.command("addons")
def migrate_addons_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without saving."),
    force: bool = typer.Option(False, "--force", help="Re-run even if already marked complete."),
) -> None:
    """
    Migrate legacy addons/ → templates/ architecture.

    Converts addon.json files to template.yaml format in both the
    repository and per-host user directories.
    """
    step = "addons"
    if _is_done(step) and not force:
        ch.success(f"Migration '{step}' already complete (use --force to re-run).")
        return

    try:
        from navig.migrations.migrate_addons_to_templates import (
            AddonToTemplateMigration,  # noqa: PLC0415
        )
        ok = AddonToTemplateMigration(dry_run=dry_run, force=force).run()
    except Exception as exc:
        ch.error(f"Migration '{step}' failed: {exc}")
        raise typer.Exit(1) from exc

    if not dry_run and ok:
        _mark_done(step)
        ch.success(f"Migration '{step}' complete.")
    elif not ok:
        ch.warning(f"Migration '{step}' finished with errors — not marked complete.")
        raise typer.Exit(1)


# ── migrate all ───────────────────────────────────────────────────────────────

@migrate_app.command("all")
def migrate_all_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview all steps without saving."),
    force: bool = typer.Option(False, "--force", help="Re-run all steps even if already done."),
) -> None:
    """
    Run all migration steps in dependency order.

    Each step is idempotent — already-complete steps are skipped
    unless --force is given.
    """
    ch.heading("NAVIG Migration Pipeline")
    if dry_run:
        ch.warning("DRY RUN — no changes will be saved.")

    errors: list[str] = []
    for name, description in MIGRATION_STEPS:
        ch.info(f"Step: {name} — {description}")
        try:
            # Invoke each step via its Typer command function
            if name == "config":
                migrate_config_cmd(dry_run=dry_run, force=force)
            elif name == "addons":
                migrate_addons_cmd(dry_run=dry_run, force=force)
        except SystemExit as exc:
            if exc.code not in (None, 0):
                ch.error(f"Step '{name}' failed.")
                errors.append(name)

    if errors:
        ch.error(f"Pipeline completed with failures: {', '.join(errors)}")
        raise typer.Exit(1)
    else:
        ch.success("All migration steps complete.")


# ── migrate status ────────────────────────────────────────────────────────────

@migrate_app.command("status")
def migrate_status_cmd() -> None:
    """Show which migration steps have been completed."""
    path = _done_file()
    done = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()

    ch.heading("Migration Status")
    for name, description in MIGRATION_STEPS:
        mark = "[green]✓[/green]" if name in done else "[yellow]○[/yellow]"
        from rich.console import Console  # noqa: PLC0415
        Console().print(f"  {mark} {name:<16} {description}")
