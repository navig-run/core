"""navig boot — boot sequence configuration and startup hooks."""
import typer
from rich.console import Console

boot_app = typer.Typer(help="Configure NAVIG boot sequence and startup hooks", no_args_is_help=True)
console = Console()


@boot_app.command("show")
def boot_show():
    """Show the current boot configuration."""
    from navig import console_helper as ch

    ch.warn("navig boot is not yet implemented in this build.")


@boot_app.command("run")
def boot_run(dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing")):
    """Execute the boot sequence hooks."""
    from navig import console_helper as ch

    ch.warn("navig boot run is not yet implemented in this build.")
