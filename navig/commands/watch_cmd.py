"""navig watch — unified event observation system."""
import typer

watch_app = typer.Typer(help="Observe and react to filesystem and system events", no_args_is_help=True)


@watch_app.command("start")
def watch_start(
    path: str = typer.Argument(".", help="Path to watch"),
):
    """Start watching a path for changes."""
    from navig import console_helper as ch

    ch.warn("navig watch is not yet implemented in this build.")


@watch_app.command("list")
def watch_list():
    """List active watches."""
    from navig import console_helper as ch

    ch.warn("navig watch list is not yet implemented in this build.")
