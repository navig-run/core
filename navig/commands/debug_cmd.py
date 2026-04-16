"""navig debug — toggle debug mode, show log sizes, open log files."""
from __future__ import annotations


import typer

from navig.console_helper import get_console
from navig.platform.paths import config_dir

debug_app = typer.Typer(help="Debug mode and observability tools", no_args_is_help=False)
console = get_console()


@debug_app.callback(invoke_without_command=True)
def debug_default(ctx: typer.Context):
    """Show debug log info."""
    if ctx.invoked_subcommand:
        return
    log_dir = config_dir() / "logs"
    debug_log = config_dir() / "debug.log"
    if debug_log.exists():
        size = debug_log.stat().st_size
        console.print(f"[cyan]debug.log[/cyan]  {size:,} bytes  [dim]{debug_log}[/dim]")
    else:
        console.print("[dim]No debug.log found.[/dim]")
    if log_dir.exists():
        logs = sorted(log_dir.glob("*.json")) + sorted(log_dir.glob("*.log"))
        for f in logs[:10]:
            console.print(f"  [dim]{f.name}[/dim]  {f.stat().st_size:,} B")


@debug_app.command("tail")
def debug_tail(lines: int = typer.Option(50, "--lines", "-n", help="Lines to tail")):
    """Tail the NAVIG debug log."""
    debug_log = config_dir() / "debug.log"
    if not debug_log.exists():
        console.print("[yellow]No debug.log found.[/yellow]")
        return
    all_lines = debug_log.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in all_lines[-lines:]:
        console.print(line)


@debug_app.command("clear")
def debug_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear the NAVIG debug log."""
    debug_log = config_dir() / "debug.log"
    if not debug_log.exists():
        console.print("[dim]Nothing to clear.[/dim]")
        return
    if not yes:
        typer.confirm("Clear debug.log?", abort=True)
    debug_log.write_text("", encoding="utf-8")
    console.print("[green]debug.log cleared.[/green]")
