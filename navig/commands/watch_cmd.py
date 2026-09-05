"""navig watch — unified event observation system."""
import typer

watch_app = typer.Typer(help="Observe and react to filesystem and system events", no_args_is_help=True)


@watch_app.command("start")
def watch_start(
    path: str = typer.Argument(".", help="Path to watch"),
):
    """Start watching a path for changes."""
    from navig import console_helper as ch

    ch.warning("`watch` is not implemented — no file-watch backend ships yet; for scheduled runs use: navig cron")
    raise typer.Exit(1)


@watch_app.command("list")
def watch_list():
    """List active watches."""
    from navig import console_helper as ch

    ch.warning("`watch list` is not implemented — no file-watch backend ships yet; see: navig cron list")
    raise typer.Exit(1)
