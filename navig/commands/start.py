from __future__ import annotations

import typer

from navig.commands.space import space_switch


app = typer.Typer(
    name="start",
    help="Start work in a space and show immediate next actions.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def start_space(
    ctx: typer.Context,
    space: str | None = typer.Argument(None, help="Space to activate"),
) -> None:
    """Activate a space and print kickoff next actions."""
    if ctx.invoked_subcommand is not None:
        return

    if not space:
        print(ctx.get_help())
        raise typer.Exit(1)

    space_switch(space)
