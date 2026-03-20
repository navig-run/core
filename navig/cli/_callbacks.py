"""Help, schema, and version callbacks for the NAVIG CLI."""
from __future__ import annotations

from typing import Optional

import typer

from navig import __version__
from navig.lazy_loader import lazy_import
from navig.cli.help_dictionaries import HELP_REGISTRY

ch = lazy_import("navig.console_helper")
_HACKER_QUOTES: list | None = None


def show_subcommand_help(name: str, ctx: Optional[typer.Context] = None):
    """Display compact help for a subcommand using the help registry."""
    from rich.console import Console
    from rich.table import Table

    console = Console(legacy_windows=True)

    if name not in HELP_REGISTRY:
        return False

    info = HELP_REGISTRY[name]

    console.print()
    console.print(f"[bold cyan]navig {name}[/bold cyan] [dim]-[/dim] [white]{info['desc']}[/white]")
    console.print("[dim]" + "=" * 75 + "[/dim]")

    cmd_table = Table(box=None, show_header=False, padding=(0, 2), collapse_padding=True)
    cmd_table.add_column("Command", style="cyan", min_width=12)
    cmd_table.add_column("Description", style="dim")

    for cmd, desc in info["commands"].items():
        cmd_table.add_row(cmd, desc)

    console.print(cmd_table)
    console.print("[dim]" + "=" * 75 + "[/dim]")
    console.print(f"[yellow]navig {name} <cmd> --help[/yellow] [dim]for command details[/dim]")
    console.print()

    return True


def make_subcommand_callback(name: str):
    """Create a callback function for a subcommand that shows custom help."""
    def callback(ctx: typer.Context):
        if ctx.invoked_subcommand is None:
            if show_subcommand_help(name, ctx):
                raise typer.Exit()
    return callback


def show_compact_help():
    """Display domain-grouped help using the registry renderer."""
    try:
        from navig.cli.help import render_root_help
        render_root_help()
    except Exception:
        from navig import __version__ as _version
        typer.echo(f"NAVIG v{_version}")
        typer.echo("  navig <command> [options]")
        typer.echo("  navig help <cmd>  for details")
    raise typer.Exit()


def help_callback(ctx: typer.Context, value: bool):
    """Callback for `--help` flag."""
    if value:
        show_compact_help()


def _get_hacker_quotes() -> list:
    global _HACKER_QUOTES
    if _HACKER_QUOTES is None:
        from navig.cli._quotes import HACKER_QUOTES as quotes
        _HACKER_QUOTES = quotes
    return _HACKER_QUOTES


def _schema_callback(value: bool):
    """Output machine-readable command schema as JSON and exit."""
    if value:
        import json as _json
        from navig.cli.registry import get_schema
        schema = get_schema()
        typer.echo(_json.dumps(schema, indent=2))
        raise typer.Exit()


def version_callback(value: bool):
    """Show version and exit."""
    if value:
        ch.info(f"NAVIG v{__version__}")
        import random
        quote, author = random.choice(_get_hacker_quotes())
        ch.dim(f'💬 {quote} - {author}')
        raise typer.Exit()
