"""navig portable — manage portable / offline NAVIG installations."""
import typer

from navig.console_helper import get_console

portable_app = typer.Typer(help="Manage portable NAVIG installations (USB / offline)", no_args_is_help=True)
console = get_console()


@portable_app.command("create")
def portable_create(
    output: str = typer.Argument("navig-portable", help="Output directory or archive name"),
):
    """Create a portable NAVIG bundle."""
    from navig import console_helper as ch

    ch.warning("`portable create` is not implemented — use: navig backup export --include-secrets --encrypt")
    raise typer.Exit(1)


@portable_app.command("validate")
def portable_validate(path: str = typer.Argument(".", help="Path to portable bundle")):
    """Validate a portable NAVIG bundle."""
    from navig import console_helper as ch

    ch.warning("`portable validate` is not implemented — inspect a bundle with: navig backup show")
    raise typer.Exit(1)
