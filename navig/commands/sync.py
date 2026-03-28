"""navig sync — configuration and state synchronisation placeholder."""
import typer

sync_app = typer.Typer(help="Sync NAVIG configuration and state across nodes", no_args_is_help=True)


@sync_app.command("status")
def sync_status():
    """Show sync status."""
    from navig import console_helper as ch

    ch.warn("navig sync is not yet implemented in this build.")
