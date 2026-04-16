"""Help, schema, and version callbacks for the NAVIG CLI."""

from __future__ import annotations

import logging
import random

import typer

from navig import __version__
from navig.cli.help_dictionaries import HELP_REGISTRY
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")
_log = logging.getLogger(__name__)

# Lazily loaded on first version display to keep import cost zero on every
# other invocation.
_HACKER_QUOTES: list[tuple[str, str]] | None = None


def show_subcommand_help(name: str, ctx: typer.Context | None = None) -> bool:
    """Display compact help for *name* using the help registry.

    Returns ``True`` when help was printed, ``False`` when the command is not
    in the registry.
    """
    if name not in HELP_REGISTRY:
        return False

    from rich.console import Console
    from rich.table import Table

    info = HELP_REGISTRY[name]
    console = Console()

    console.print()
    console.print(
        f"[bold cyan]navig {name}[/bold cyan]"
        f" [dim]-[/dim] [white]{info['desc']}[/white]"
    )
    console.print("[dim]" + "=" * 75 + "[/dim]")

    cmd_table = Table(
        box=None, show_header=False, padding=(0, 2), collapse_padding=True
    )
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
    """Return a Typer callback that shows custom help for *name* when no subcommand is given."""

    def callback(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            if show_subcommand_help(name, ctx):
                raise typer.Exit()

    return callback


def show_compact_help() -> None:
    """Render ``navig/help/index.md`` with Rich Markdown, falling back to plain text."""
    from pathlib import Path

    help_index = Path(__file__).resolve().parent.parent / "help" / "index.md"
    if help_index.exists():
        try:
            from rich.console import Console
            from rich.markdown import Markdown

            Console().print(Markdown(help_index.read_text(encoding="utf-8")))
            raise typer.Exit()
        except typer.Exit:
            raise
        except Exception as exc:
            _log.debug(
                "rich help rendering failed; falling back to plain text: %s", exc
            )

    typer.echo(f"NAVIG v{__version__}")
    typer.echo("  navig <command> [options]")
    typer.echo("  navig help <cmd>  for details")
    raise typer.Exit()


def help_callback(ctx: typer.Context, value: bool) -> None:
    """Typer callback for the ``--help`` flag."""
    if value:
        show_compact_help()


def _get_hacker_quotes() -> list[tuple[str, str]]:
    """Return the hacker-quotes list, loading it on first call."""
    global _HACKER_QUOTES
    if _HACKER_QUOTES is None:
        from navig.cli._quotes import HACKER_QUOTES

        _HACKER_QUOTES = HACKER_QUOTES
    return _HACKER_QUOTES


def _schema_callback(value: bool) -> None:
    """Output the machine-readable command schema as JSON and exit."""
    if value:
        import json

        from navig.cli.registry import get_schema

        typer.echo(json.dumps(get_schema(), indent=2))
        raise typer.Exit()


def version_callback(value: bool) -> None:
    """Show version and a random hacker quote, then exit."""
    if not value:
        return
    try:
        ch.info(f"NAVIG v{__version__}")
        quote, author = random.choice(_get_hacker_quotes())
        ch.dim(f"💬 {quote} - {author}")
    except Exception:
        # Fallback when Rich / console_helper is unavailable.
        typer.echo(f"NAVIG v{__version__}")
    raise typer.Exit()
