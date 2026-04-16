"""navig blueprint — scaffold and manage project blueprints."""
import typer

from navig.console_helper import get_console

blueprint_app = typer.Typer(help="Manage project blueprints and templates", no_args_is_help=True)
console = get_console()


@blueprint_app.command("list")
def blueprint_list():
    """List available blueprints."""
    from navig import console_helper as ch

    ch.warn("navig blueprint is not yet implemented in this build.")


@blueprint_app.command("apply")
def blueprint_apply(
    name: str = typer.Argument(..., help="Blueprint name"),
    target: str = typer.Option(".", "--target", "-t", help="Target directory"),
):
    """Apply a blueprint to a directory."""
    from navig import console_helper as ch

    ch.warn("navig blueprint apply is not yet implemented in this build.")
