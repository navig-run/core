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
from navig.console_helper import get_console
from navig.spaces.kickoff import build_space_kickoff

# ── Typer app ─────────────────────────────────────────────────────────────────

space_app = typer.Typer(
    name="space",
    help="Manage NAVIG spaces (multi-context environments).",
    invoke_without_command=True,
    no_args_is_help=False,
)

_console = get_console()

# Slug: lowercase letters/digits/hyphens, must start with letter or digit
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,28}[a-z0-9])?$")
_BUILTIN_SPACES = ("default", "personal", "work", "focus", "studio")

_DEFAULT_INDEX_MD = """\
# My Space

> Capture how you work, what you’re focused on, and anything Navig should know about you.

## About Me
<!-- Who you are, how you work -->

## How I Use Navig
<!-- Workflow, preferred spaces, shortcuts -->

## Current Focus
<!-- Active projects, priorities, deadlines -->

## Quick Links
<!-- Pinned resources, frequent destinations -->
"""

_DEFAULT_VISION_MD = """# Vision

> What are you working toward?
"""

_DEFAULT_PHASE_MD = """# Current Phase

> What phase are you in right now?
"""


# ── Internal helpers ──────────────────────────────────────────────────────────


def _spaces_dir(create: bool = True) -> Path:
    """Return ``~/.navig/spaces/``, creating it when *create* is ``True``."""
    d = Path(get_config_manager().global_config_dir) / "spaces"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _suggest_builtins() -> str:
    return "Tip: Common spaces \u2014 default (My Space), " + ", ".join(s for s in _BUILTIN_SPACES if s != "default")


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
            pass  # best-effort: skip on IO error
    try:
        cfg = get_config_manager().global_config or {}
        if isinstance(cfg, dict):
            space_cfg = cfg.get("space", {})
            if isinstance(space_cfg, dict):
                name = str(space_cfg.get("active", "")).strip()
                if name:
                    return name

            name = str(cfg.get("active_space", "")).strip()
            if name:
                return name

            spaces_cfg = cfg.get("spaces", {})
            if isinstance(spaces_cfg, dict):
                name = str(spaces_cfg.get("active", "")).strip()
                if name:
                    return name
    except Exception:  # noqa: BLE001
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
        space_cfg = gc.get("space", {})
        if not isinstance(space_cfg, dict):
            space_cfg = {}
        space_cfg["active"] = name
        gc["space"] = space_cfg
        gc["active_space"] = name

        legacy_spaces = gc.get("spaces", {})
        if isinstance(legacy_spaces, dict):
            legacy_spaces.pop("active", None)
            if legacy_spaces:
                gc["spaces"] = legacy_spaces
            else:
                gc.pop("spaces", None)

        config_file = Path(cm.global_config_dir) / "config.yaml"
        config_file.write_text(
            yaml.dump(gc, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass  # cache file is the source of truth; config.yaml update is best-effort


def _ensure_default_space() -> None:
    """Create ``~/.navig/spaces/default/`` and scaffold starter files on first use."""
    default_dir = _spaces_dir() / "default"
    default_dir.mkdir(parents=True, exist_ok=True)

    # Only write each file if it does not exist — never overwrite user content
    index_file = default_dir / "index.md"
    if not index_file.exists():
        index_file.write_text(_DEFAULT_INDEX_MD, encoding="utf-8")

    vision_file = default_dir / "VISION.md"
    if not vision_file.exists():
        vision_file.write_text(_DEFAULT_VISION_MD, encoding="utf-8")

    phase_file = default_dir / "CURRENT_PHASE.md"
    if not phase_file.exists():
        phase_file.write_text(_DEFAULT_PHASE_MD, encoding="utf-8")


def _default_hint_file() -> Path:
    return Path(get_config_manager().global_config_dir) / "cache" / ".default_space_hint_shown"


def _maybe_show_default_hint() -> None:
    """Emit a one-time non-blocking prompt when the default space is still uncustomised."""
    hint_file = _default_hint_file()
    if hint_file.exists():
        return

    default_index = _spaces_dir(create=False) / "default" / "index.md"
    if not default_index.exists():
        return

    content = default_index.read_text(encoding="utf-8").strip()
    # Only show hint when file contains only the starter template (no user edits)
    if content and content == _DEFAULT_INDEX_MD.strip():
        ch.info(
            "This is your space \u2014 add context, goals, and notes so Navig works better for you.",
            details=f"Edit: navig file edit {default_index}",
        )
        try:
            hint_file.parent.mkdir(parents=True, exist_ok=True)
            hint_file.write_text("shown", encoding="utf-8")
        except OSError:
            pass  # best-effort: skip on IO error
def _validate_slug(name: str) -> str:
    value = (name or "").strip().lower()
    if _SLUG_RE.match(value):
        return value
    raise typer.BadParameter(
        f"Invalid space name `{name}`. Use lowercase letters, digits, hyphens.\n{_suggest_builtins()}"
    )


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
@space_app.command("create")
def space_create(
    name: str = typer.Argument(..., help="Space name — slug format: a-z0-9 and hyphens"),
) -> None:
    """Create a new named space."""
    name = _validate_slug(name)

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
    name = _validate_slug(name)
    space_path = _spaces_dir(create=False) / name
    if not space_path.exists():
        ch.error(
            f"Space '{name}' does not exist.",
            details=f"Run `navig space new {name}` to create it first.",
        )
        raise typer.Exit(1)

    _set_active_space(name)
    ch.success(f"Active space: {name}", details=str(space_path))

    if name == "default":
        _maybe_show_default_hint()

    kickoff = build_space_kickoff(name, space_path, cwd=Path.cwd(), max_items=3)
    if kickoff.actions:
        ch.info(f"Goal: {kickoff.goal}")
        ch.info("Top next actions:")
        for index, action in enumerate(kickoff.actions, start=1):
            ch.info(f"{index}. {action}")
    else:
        ch.info("No next actions found yet. Add tasks in CURRENT_PHASE.md or .navig/plans/*.md.")


@space_app.command("delete")
def space_delete(
    name: str = typer.Argument(..., help="Space name to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete a space and all its contents."""
    name = _validate_slug(name)
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


@space_app.command("current")
def space_current() -> None:
    """Show the active space (NAVIG_SPACE override respected)."""
    _ensure_default_space()
    active = get_active_space()
    label = "My Space (default)" if active == "default" else active
    ch.info(f"Active space: {label}")
    if active == "default":
        _maybe_show_default_hint()


@space_app.command("use")
def space_use(
    name: str = typer.Argument(..., help="Space name to activate"),
) -> None:
    """Compatibility alias for `navig space switch <name>`."""
    space_switch(name)


# Backward-compatible function name used by tests/importers.
space_new = space_create
