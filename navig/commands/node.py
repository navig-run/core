"""navig node — manage NAVIG compute nodes in a formation."""
import typer
from rich.console import Console

node_app = typer.Typer(help="Manage compute nodes in the NAVIG formation", no_args_is_help=True)
console = Console()


@node_app.command("list")
def node_list():
    """List known nodes."""
    from navig import console_helper as ch

    ch.warn("navig node is not yet implemented in this build.")


@node_app.command("add")
def node_add(address: str = typer.Argument(..., help="Node address (host:port)")):
    """Register a new node."""
    from navig import console_helper as ch

    ch.warn("navig node add is not yet implemented in this build.")


@node_app.command("remove")
def node_remove(name: str = typer.Argument(..., help="Node name")):
    """Remove a registered node."""
    from navig import console_helper as ch

    ch.warn("navig node remove is not yet implemented in this build.")
