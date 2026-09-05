"""navig radar — generic mention and keyword tracker."""
import typer

radar_app = typer.Typer(help="Track mentions and keywords across channels", no_args_is_help=True)


@radar_app.command("list")
def radar_list():
    """List active radar watches."""
    from navig import console_helper as ch

    ch.warning("`radar` is not implemented — no mention-tracking backend ships yet.")
    raise typer.Exit(1)


@radar_app.command("add")
def radar_add(keyword: str = typer.Argument(..., help="Keyword or pattern to watch")):
    """Add a keyword to radar."""
    from navig import console_helper as ch

    ch.warning("`radar add` is not implemented — no mention-tracking backend ships yet.")
    raise typer.Exit(1)
