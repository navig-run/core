"""navig agents — manage NAVIG sub-agents and formation agents."""
import typer

app = typer.Typer(help="Manage NAVIG sub-agents", no_args_is_help=True)


@app.command("list")
def agents_list():
    """List registered agents."""
    from navig import console_helper as ch

    try:
        from navig.agents import list_agents  # type: ignore[import]

        for agent in list_agents():
            typer.echo(f"  {agent}")
    except Exception:
        ch.warn("navig agents is not yet fully implemented in this build.")


@app.command("run")
def agents_run(
    name: str = typer.Argument(..., help="Agent name"),
    task: str = typer.Argument("", help="Task description"),
):
    """Run a specific agent on a task."""
    from navig import console_helper as ch

    ch.warn("navig agents run is not yet implemented in this build.")
