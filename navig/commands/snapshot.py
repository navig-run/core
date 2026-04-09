"""navig snapshot — create and restore system/config snapshots."""
import typer
from rich.console import Console

from navig.console_helper import get_console

app = typer.Typer(help="Create and restore NAVIG configuration snapshots", no_args_is_help=True)
console = get_console()


@app.command("create")
def snapshot_create(
    name: str = typer.Argument("", help="Snapshot name (auto-generated if omitted)"),
):
    """Create a named snapshot of the current NAVIG config and state."""
    from navig import console_helper as ch

    ch.warn("navig snapshot create is not yet implemented in this build.")


@app.command("list")
def snapshot_list():
    """List available snapshots."""
    from navig import console_helper as ch

    ch.warn("navig snapshot list is not yet implemented in this build.")


@app.command("restore")
def snapshot_restore(name: str = typer.Argument(..., help="Snapshot name to restore")):
    """Restore a snapshot."""
    from navig import console_helper as ch

    ch.warn("navig snapshot restore is not yet implemented in this build.")
