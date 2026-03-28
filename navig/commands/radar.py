"""navig radar — generic mention and keyword tracker."""
import typer

radar_app = typer.Typer(help="Track mentions and keywords across channels", no_args_is_help=True)


@radar_app.command("list")
def radar_list():
    """List active radar watches."""
    from navig import console_helper as ch

    ch.warn("navig radar is not yet implemented in this build.")


@radar_app.command("add")
def radar_add(keyword: str = typer.Argument(..., help="Keyword or pattern to watch")):
    """Add a keyword to radar."""
    from navig import console_helper as ch

    ch.warn("navig radar add is not yet implemented in this build.")
