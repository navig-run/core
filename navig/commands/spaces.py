"""navig spaces — personal / workspace / studio context switcher."""
import typer

from navig.console_helper import get_console

spaces_context_app = typer.Typer(help="Switch between personal, workspace, and studio contexts", no_args_is_help=False)
console = get_console()

_VALID_SPACES = ("personal", "workspace", "studio", "focus")


@spaces_context_app.callback(invoke_without_command=True)
def spaces_default(ctx: typer.Context):
    """Show the active space context."""
    if ctx.invoked_subcommand:
        return
    try:
        from navig.config import ConfigManager

        cfg = ConfigManager()
        current = cfg.get("spaces.active", default="personal")
        console.print(f"Active space: [cyan]{current}[/cyan]")
    except Exception as exc:
        console.print(f"[yellow]Could not read active space: {exc}[/yellow]")


@spaces_context_app.command("use")
def spaces_use(name: str = typer.Argument(..., help="Space name")):
    """Switch to a space context."""
    if name not in _VALID_SPACES:
        console.print(f"[yellow]Unknown space '{name}'. Valid: {', '.join(_VALID_SPACES)}[/yellow]")
        raise typer.Exit(1)
    try:
        from navig.config import ConfigManager

        cfg = ConfigManager()
        cfg.set("spaces.active", name)
        console.print(f"[green]Switched to space:[/green] {name}")
    except Exception as exc:
        console.print(f"[red]Failed to switch space: {exc}[/red]")
        raise typer.Exit(1) from exc


@spaces_context_app.command("list")
def spaces_list():
    """List available spaces."""
    for s in _VALID_SPACES:
        console.print(f"  {s}")
