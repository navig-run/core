"""navig cloud — cloud provider integration (AWS, GCP, Azure, Hetzner …)."""
import typer

app = typer.Typer(help="Cloud provider integrations", no_args_is_help=True)


@app.command("status")
def cloud_status():
    """Show cloud connection status."""
    from navig import console_helper as ch

    ch.warn("navig cloud is not yet implemented in this build.")


@app.command("list")
def cloud_list():
    """List configured cloud providers."""
    from navig import console_helper as ch

    ch.warn("navig cloud list is not yet implemented in this build.")
