"""navig origin — show formation / installation origin details."""
import typer
from rich.console import Console

origin_app = typer.Typer(help="Show installation origin and formation lineage", no_args_is_help=False)
console = Console()


@origin_app.callback(invoke_without_command=True)
def origin_default(ctx: typer.Context):
    """Show where this NAVIG installation came from."""
    if ctx.invoked_subcommand:
        return
    from navig import console_helper as ch

    ch.warn("navig origin is not yet implemented in this build.")
