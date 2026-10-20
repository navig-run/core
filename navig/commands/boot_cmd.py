"""navig boot — SUPERSEDED. There is no boot sequence to configure: NAVIG starts as the
background service (`navig service status` · `navig service install` · `navig service start`).

Both verbs were stubs that printed "not yet implemented". The group stays registered so
old invocations get a pointer — each prints the real command and exits 1.
"""

import typer

from navig.cli.superseded import moved

boot_app = typer.Typer(
    help="NAVIG starts as the background service (moved → navig service)",
    invoke_without_command=True,
    no_args_is_help=False,
)

_ANY_ARGS = {"ignore_unknown_options": True, "allow_extra_args": True}


@boot_app.callback()
def _boot_callback(ctx: typer.Context) -> None:
    moved(f"navig boot {ctx.invoked_subcommand}" if ctx.invoked_subcommand else "navig boot")


@boot_app.command("show", context_settings=_ANY_ARGS)
def boot_show() -> None:
    """Moved to `navig service status`."""
    moved("navig boot show")


@boot_app.command("run", context_settings=_ANY_ARGS)
def boot_run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Ignored (kept for compatibility)"),
) -> None:
    """Moved to `navig service start`."""
    moved("navig boot run")
