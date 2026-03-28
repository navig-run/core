"""navig portable — manage portable / offline NAVIG installations."""
import typer
from rich.console import Console

portable_app = typer.Typer(help="Manage portable NAVIG installations (USB / offline)", no_args_is_help=True)
console = Console()


@portable_app.command("create")
def portable_create(
    output: str = typer.Argument("navig-portable", help="Output directory or archive name"),
):
    """Create a portable NAVIG bundle."""
    from navig import console_helper as ch

    ch.warn("navig portable create is not yet implemented in this build.")


@portable_app.command("validate")
def portable_validate(path: str = typer.Argument(".", help="Path to portable bundle")):
    """Validate a portable NAVIG bundle."""
    from navig import console_helper as ch

    ch.warn("navig portable validate is not yet implemented in this build.")
