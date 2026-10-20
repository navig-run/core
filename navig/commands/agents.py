"""navig agents — SUPERSEDED. Agents live in formations: `navig formation agents` lists
them and `navig agent run <id> --task …` runs one.

Neither verb here ever worked (`list` imported a function that does not exist and
printed a warning with exit 0; `run` said "not implemented"). The group stays
registered so old invocations get a pointer instead of "No such command" — each verb
prints where the real command is and exits 1 (see ``navig.cli.superseded``).
"""

import typer

from navig.cli.superseded import moved

app = typer.Typer(
    help="Formation agents (moved → navig formation agents / navig agent run)",
    invoke_without_command=True,
    no_args_is_help=False,
)

# Accept whatever the old verbs accepted: the callback answers before any parsing.
_ANY_ARGS = {"ignore_unknown_options": True, "allow_extra_args": True}


@app.callback()
def _agents_callback(ctx: typer.Context) -> None:
    moved(f"navig agents {ctx.invoked_subcommand}" if ctx.invoked_subcommand else "navig agents")


@app.command("list", context_settings=_ANY_ARGS)
def agents_list() -> None:
    """Moved to `navig formation agents`."""
    moved("navig agents list")


@app.command("run", context_settings=_ANY_ARGS)
def agents_run() -> None:
    """Moved to `navig agent run <agent_id> --task <task>`."""
    moved("navig agents run")
