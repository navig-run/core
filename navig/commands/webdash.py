"""navig webdash — launch the NAVIG web dashboard."""
import typer
from rich.console import Console

from navig.console_helper import get_console

app = typer.Typer(help="Launch the NAVIG web dashboard", no_args_is_help=False)
console = get_console()


@app.callback(invoke_without_command=True)
def webdash_default(
    ctx: typer.Context,
    port: int = typer.Option(7002, "--port", "-p", help="Dashboard port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
):
    """Start the NAVIG web dashboard."""
    if ctx.invoked_subcommand:
        return
    console.print(f"[bold]Starting NAVIG web dashboard[/bold] on http://{host}:{port}")
    try:
        from navig.api.server import run_api_server  # type: ignore[import]

        run_api_server(host=host, port=port)
    except ImportError:
        console.print("[yellow]Web dashboard not available in this build.[/yellow]")
        raise typer.Exit(1) from None
