"""navig webdash — SUPERSEDED by `navig deck open`.

`webdash` started a SECOND gateway (``navig.api.server.run_api_server`` boots a real
``NavigGateway``) just to serve a dashboard the running daemon already serves. It now
says where the dashboard is and exits 1 instead (see ``navig.cli.superseded``).
"""

import typer

from navig.cli.superseded import moved

app = typer.Typer(
    help="The dashboard is served by the running daemon (moved → navig deck open)",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def webdash_default(
    ctx: typer.Context,
    # Still accepted, so an old `navig webdash --port 7002` gets the pointer rather
    # than a usage error. Neither does anything any more.
    port: int = typer.Option(7002, "--port", "-p", help="Ignored (kept for compatibility)"),
    host: str = typer.Option("127.0.0.1", "--host", help="Ignored (kept for compatibility)"),
) -> None:
    """Moved to `navig deck open`."""
    moved("navig webdash")
