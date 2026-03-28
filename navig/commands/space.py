"""navig space — multi-context space management.

Spaces live under ~/.navig/spaces/<name>/ and let you maintain separate
environments (e.g. homelab, client-x, default) within a single NAVIG install.

Active space resolution order:
  1. NAVIG_SPACE environment variable  (CI / scripting override)
  2. ~/.navig/cache/active_space.txt   (persisted by ``navig space switch``)
  3. "default"                         (zero-config fallback)
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from navig import console_helper as ch
from navig.config import get_config_manager

# ── Typer app ─────────────────────────────────────────────────────────────────

space_app = typer.Typer(
    name="space",
    help="Manage NAVIG spaces (multi-context environments).",
    invoke_without_command=True,
    no_args_is_help=False,
)

_console = Console()

# Slug: lowercase letters/digits/hyphens, must start with letter or digit
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


# ── Internal helpers ──────────────────────────────────────────────────────────


def _spaces_dir(create: bool = True) -> Path:
    """Return ``~/.navig/spaces/``, creating it when *create* is ``True``."""
    d = Path(get_config_manager().global_config_dir) / "spaces"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _active_space_cache_file() -> Path:
    return Path(get_config_manager().global_config_dir) / "cache" / "active_space.txt"


def get_active_space() -> str:
    """Return the active space name.

    Respects the ``NAVIG_SPACE`` environment variable so CI/scripting callers
    can override without touching the local state file.
    """
    env = os.environ.get("NAVIG_SPACE", "").strip()
    if env:
        return env

    cache_file = _active_space_cache_file()
    if cache_file.exists():
        try:
            name = cache_file.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass

    return "default"


def _set_active_space(name: str) -> None:
    """Persist *name* as the active space (cache file + best-effort config.yaml)."""
    cache_file = _active_space_cache_file()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(name, encoding="utf-8")

    # Best-effort: mirror into ~/.navig/config.yaml so `navig config show` reflects it
    try:
        import yaml

        cm = get_config_manager()
        gc = dict(cm.global_config)
        gc["active_space"] = name
        config_file = Path(cm.global_config_dir) / "config.yaml"
        config_file.write_text(
            yaml.dump(gc, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass  # cache file is the source of truth; config.yaml update is best-effort


def _ensure_default_space() -> None:
    """Create ``~/.navig/spaces/default/`` on first use (zero-config)."""
    (_spaces_dir() / "default").mkdir(parents=True, exist_ok=True)


def _validate_slug(name: str) -> bool:
    return bool(_SLUG_RE.match(name))


# ── Default callback — `navig space` → `navig space list` ────────────────────


@space_app.callback()
def _space_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            _space_list()
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("space", space_app)


# ── Commands ──────────────────────────────────────────────────────────────────


@space_app.command("list")
def _space_list() -> None:
    """List all spaces with active indicator."""
    _ensure_default_space()
    spaces_dir = _spaces_dir()
    active = get_active_space()

    entries = sorted(p.name for p in spaces_dir.iterdir() if p.is_dir())

    if not entries:
        ch.warning(
            "No spaces found.",
            details="Run `navig space new <name>` to create one.",
        )
        return

    ch.info(f"Active space: {active}", details=str(spaces_dir / active))

    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="dim")

    for name in entries:
        marker = "▸" if name == active else " "
        table.add_row(f"{marker} {name}", str(spaces_dir / name))

    _console.print(table)


@space_app.command("new")
def space_new(
    name: str = typer.Argument(..., help="Space name — slug format: a-z0-9 and hyphens"),
) -> None:
    """Create a new named space."""
    if not _validate_slug(name):
        ch.error(
            f"Invalid space name: {name!r}",
            details="Use lowercase letters, digits, and hyphens only (a-z0-9-).",
        )
        raise typer.Exit(1)

    space_path = _spaces_dir() / name
    if space_path.exists():
        ch.warning(f"Space '{name}' already exists.", details=str(space_path))
        return

    try:
        space_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        ch.error(f"Failed to create space '{name}'.", details=str(exc))
        raise typer.Exit(1) from exc

    ch.success(f"Created space '{name}'.", details=str(space_path))


@space_app.command("switch")
def space_switch(
    name: str = typer.Argument(..., help="Space name to activate"),
) -> None:
    """Activate a space."""
    space_path = _spaces_dir(create=False) / name
    if not space_path.exists():
        ch.error(
            f"Space '{name}' does not exist.",
            details=f"Run `navig space new {name}` to create it first.",
        )
        raise typer.Exit(1)

    _set_active_space(name)
    ch.success(f"Active space: {name}", details=str(space_path))


@space_app.command("delete")
def space_delete(
    name: str = typer.Argument(..., help="Space name to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete a space and all its contents."""
    if name == "default":
        ch.error("Cannot delete the 'default' space.")
        raise typer.Exit(1)

    space_path = _spaces_dir(create=False) / name
    if not space_path.exists():
        ch.error(f"Space '{name}' does not exist.")
        raise typer.Exit(1)

    if not yes:
        confirmed = typer.confirm(
            f"Delete space '{name}' at {space_path}? This cannot be undone.",
            default=False,
        )
        if not confirmed:
            ch.info("Aborted.")
            raise typer.Exit()

    try:
        shutil.rmtree(space_path)
    except OSError as exc:
        ch.error(f"Failed to delete space '{name}'.", details=str(exc))
        raise typer.Exit(1) from exc

    ch.success(f"Deleted space '{name}'.")

    # If the deleted space was active, fall back to default
    try:
        cached = _active_space_cache_file().read_text(encoding="utf-8").strip()
    except OSError:
        cached = ""
    if cached == name:
        _set_active_space("default")
        ch.info("Active space reset to 'default'.")
