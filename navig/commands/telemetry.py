"""navig telemetry — opt-in usage analytics."""
import typer
from rich.console import Console

telemetry_app = typer.Typer(help="Manage NAVIG telemetry / analytics opt-in", no_args_is_help=False)
console = Console()


@telemetry_app.callback(invoke_without_command=True)
def telemetry_default(ctx: typer.Context):
    """Show telemetry status."""
    if ctx.invoked_subcommand:
        return
    try:
        from navig.config import ConfigManager

        cfg = ConfigManager()
        enabled = cfg.get("telemetry.enabled", default=False)
        status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        console.print(f"Telemetry: {status}")
    except Exception:
        console.print("[dim]Telemetry: unknown[/dim]")


@telemetry_app.command("enable")
def telemetry_enable():
    """Enable telemetry."""
    try:
        from navig.config import ConfigManager

        ConfigManager().set("telemetry.enabled", True)
        console.print("[green]Telemetry enabled.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")


@telemetry_app.command("disable")
def telemetry_disable():
    """Disable telemetry."""
    try:
        from navig.config import ConfigManager

        ConfigManager().set("telemetry.enabled", False)
        console.print("[green]Telemetry disabled.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
