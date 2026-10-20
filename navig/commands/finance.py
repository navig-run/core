"""navig finance — SUPERSEDED. Finance lives in the NAVIG OS Finance app (the `finance`
module: `navig store info module:finance`); there is no finance CLI.

`balance` was a "not yet implemented" stub and `status` only reported whether the
`beancount` package happened to be importable. The group stays registered so old
invocations get a pointer — each prints where finance lives and exits 1.
"""

import typer

from navig.cli.superseded import moved

finance_app = typer.Typer(
    help="There is no finance CLI (moved → NAVIG OS Finance app)",
    invoke_without_command=True,
    no_args_is_help=False,
)

_ANY_ARGS = {"ignore_unknown_options": True, "allow_extra_args": True}


@finance_app.callback()
def _finance_callback(ctx: typer.Context) -> None:
    moved(f"navig finance {ctx.invoked_subcommand}" if ctx.invoked_subcommand else "navig finance")


@finance_app.command("status", context_settings=_ANY_ARGS)
def finance_status() -> None:
    """Moved — see `navig store info module:finance`."""
    moved("navig finance status")


@finance_app.command("balance", context_settings=_ANY_ARGS)
def finance_balance() -> None:
    """Moved — see `navig store info module:finance`."""
    moved("navig finance balance")
