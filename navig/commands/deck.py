"""navig deck — manage slide decks and presentation materials."""
import typer

from navig.console_helper import get_console

deck_app = typer.Typer(help="Manage presentation decks and slide materials", no_args_is_help=True)
console = get_console()


@deck_app.command("list")
def deck_list():
    """List available decks."""
    from navig import console_helper as ch

    ch.warn("navig deck is not yet implemented in this build.")


@deck_app.command("new")
def deck_new(name: str = typer.Argument(..., help="Deck name")):
    """Create a new deck."""
    from navig import console_helper as ch

    ch.warn("navig deck new is not yet implemented in this build.")
