"""navig system — system-level information and maintenance commands."""
from __future__ import annotations

import platform

import typer
from rich.console import Console
from rich.table import Table

from navig.console_helper import get_console
from navig.platform.paths import config_dir

system_app = typer.Typer(help="System information and maintenance", no_args_is_help=False)
console = get_console()


@system_app.callback(invoke_without_command=True)
def system_default(ctx: typer.Context):
    """Show system overview."""
    if ctx.invoked_subcommand:
        return

    table = Table(title="System Information")
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    uname = platform.uname()
    rows = [
        ("OS", f"{uname.system} {uname.release}"),
        ("Machine", uname.machine),
        ("Processor", uname.processor or "(unknown)"),
        ("Python", platform.python_version()),
        ("Node", uname.node),
    ]
    for key, value in rows:
        table.add_row(key, value)

    console.print(table)


@system_app.command("info")
def system_info():
    """Show detailed system information."""
    system_default(None)  # type: ignore[arg-type]


@system_app.command("clean")
def system_clean(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clean NAVIG caches and temporary files."""
    import shutil
    from pathlib import Path

    targets = [
        config_dir() / "cache",
        config_dir() / "__pycache__",
    ]
    if not yes:
        for t in targets:
            if t.exists():
                console.print(f"  Would remove: {t}")
        typer.confirm("Proceed?", abort=True)
    for t in targets:
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
            console.print(f"[green]Removed:[/green] {t}")
