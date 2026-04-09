"""navig paths — inspect NAVIG system paths and MCP server registration."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from navig.console_helper import get_console

paths_app = typer.Typer(help="Inspect NAVIG system paths and MCP server registration", no_args_is_help=False)
console = get_console()


@paths_app.callback(invoke_without_command=True)
def paths_default(ctx: typer.Context):
    """Show key NAVIG directory paths."""
    if ctx.invoked_subcommand:
        return

    home = Path.home()
    rows = [
        ("config",       home / ".navig" / "config.yaml"),
        ("data",         home / ".navig" / "data"),
        ("logs",         home / ".navig" / "logs"),
        ("plugins",      home / ".navig" / "plugins"),
        ("store",        home / ".navig" / "store"),
        ("wiki",         home / ".navig" / "wiki"),
        ("space",        home / ".navig" / "space"),
        ("packs",        home / ".navig" / "packs"),
    ]
    table = Table(title="NAVIG Paths")
    table.add_column("Key", style="cyan")
    table.add_column("Path")
    table.add_column("Exists", style="green")

    for key, path in rows:
        table.add_row(key, str(path), "✓" if path.exists() else "–")

    console.print(table)
