"""navig blueprint — scaffold and manage project blueprints."""
import typer

from navig.console_helper import get_console

blueprint_app = typer.Typer(help="Manage project blueprints and templates", no_args_is_help=True)
console = get_console()


@blueprint_app.command("list")
def blueprint_list():
    """List available blueprints."""
    from navig import console_helper as ch

    ch.warning("`blueprint` is not implemented — outcomes are Blocks: navig block list")
    raise typer.Exit(1)


@blueprint_app.command("apply")
def blueprint_apply(
    name: str = typer.Argument(..., help="Blueprint name"),
    target: str = typer.Option(".", "--target", "-t", help="Target directory"),
):
    """Apply a blueprint to a directory."""
    from navig import console_helper as ch

    ch.warning("`blueprint apply` is not implemented — use: navig apply <block-id>")
    raise typer.Exit(1)
