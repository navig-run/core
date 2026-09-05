"""navig node — manage NAVIG compute nodes in a formation."""
import typer

from navig.console_helper import get_console

node_app = typer.Typer(help="Manage compute nodes in the NAVIG formation", no_args_is_help=True)
console = get_console()


@node_app.command("list")
def node_list():
    """List known nodes."""
    from navig import console_helper as ch

    ch.warning("`node` is not implemented — mesh nodes are: navig mesh peers")
    raise typer.Exit(1)


@node_app.command("add")
def node_add(address: str = typer.Argument(..., help="Node address (host:port)")):
    """Register a new node."""
    from navig import console_helper as ch

    ch.warning("`node add` is not implemented — peers are discovered; see: navig mesh status")
    raise typer.Exit(1)


@node_app.command("remove")
def node_remove(name: str = typer.Argument(..., help="Node name")):
    """Remove a registered node."""
    from navig import console_helper as ch

    ch.warning("`node remove` is not implemented — peers are discovered; see: navig mesh status")
    raise typer.Exit(1)
