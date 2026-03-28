"""navig dispatch / contacts / ct — multi-network reliable message dispatch."""
import typer

dispatch_app = typer.Typer(help="Multi-network message dispatch", no_args_is_help=True)
contacts_app = typer.Typer(help="Manage contacts and address book", no_args_is_help=True)


@dispatch_app.command("send")
def dispatch_send(
    message: str = typer.Argument(..., help="Message to dispatch"),
    channel: str = typer.Option("telegram", "--channel", "-c", help="Channel (telegram|matrix|email)"),
):
    """Send a message via the configured dispatch channel."""
    from navig import console_helper as ch

    ch.warn("navig dispatch is not yet implemented in this build.")


@contacts_app.command("list")
def contacts_list():
    """List saved contacts."""
    from navig import console_helper as ch

    ch.warn("navig contacts is not yet implemented in this build.")


@contacts_app.command("add")
def contacts_add(name: str = typer.Argument(..., help="Contact name")):
    """Add a contact."""
    from navig import console_helper as ch

    ch.warn("navig contacts add is not yet implemented in this build.")
