"""navig replay — replay recorded command sessions."""
import typer
from rich.console import Console

app = typer.Typer(help="Replay recorded NAVIG command sessions", no_args_is_help=True)
console = Console()


@app.command("list")
def replay_list():
    """List recorded sessions available for replay."""
    from navig import console_helper as ch

    ch.warn("navig replay is not yet implemented in this build.")


@app.command("run")
def replay_run(
    session: str = typer.Argument(..., help="Session ID or name"),
    speed: float = typer.Option(1.0, "--speed", "-s", help="Playback speed multiplier"),
):
    """Replay a recorded session."""
    from navig import console_helper as ch

    ch.warn("navig replay run is not yet implemented in this build.")
