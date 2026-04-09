"""navig wut — show what NAVIG thinks is happening right now."""
import typer
from rich.console import Console

from navig.console_helper import get_console

app = typer.Typer(help="Show what NAVIG thinks is happening (context snapshot)", no_args_is_help=False)
console = get_console()


@app.callback(invoke_without_command=True)
def wut_default(ctx: typer.Context):
    """Print a concise context snapshot."""
    if ctx.invoked_subcommand:
        return
    try:
        from navig.config import ConfigManager

        cfg = ConfigManager()
        host = cfg.get("active_host", default="(none)")
        app_name = cfg.get("active_app", default="(none)")
        console.print(f"[bold]wut?[/bold]  host=[cyan]{host}[/cyan]  app=[cyan]{app_name}[/cyan]")
    except Exception as exc:
        console.print(f"[yellow]Context unavailable: {exc}[/yellow]")
