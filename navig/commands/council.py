"""
NAVIG Council CLI Commands

Multi-agent deliberation via the Council Engine.
"""

import json as json_module

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

council_app = typer.Typer(
    name="council",
    help="Multi-agent council deliberation",
    invoke_without_command=True,
    no_args_is_help=False,
)


@council_app.callback()
def council_callback(ctx: typer.Context):
    """Council commands - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        from navig.cli import show_subcommand_help

        show_subcommand_help("council", ctx)
        raise typer.Exit()


@council_app.command("run")
def council_run(
    question: str = typer.Argument(..., help="Question or topic for the council to deliberate"),
    rounds: int = typer.Option(1, "--rounds", "-r", help="Number of deliberation rounds (1-5)"),
    json_output: bool = typer.Option(False, "--json", help="Full JSON output"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    timeout: float | None = typer.Option(
        None, "--timeout", "-t", help="Per-agent timeout in seconds"
    ),
):
    """Run a council deliberation across all agents in the active formation.

    Each agent evaluates the question from their specialized perspective.
    The default agent synthesizes all responses into a final decision.

    Examples:
        navig council run "Should we invest in a new CRM system?"
        navig council run "Best approach for scaling the database?" --rounds 2
        navig council run "Transfer budget allocation" --json
    """
    from navig.formations.council import run_council
    from navig.formations.loader import get_active_formation

    formation = get_active_formation()
    if formation is None:
        ch.error("No active formation.")
        ch.info("  Initialize with: navig formation init <formation-id>")
        raise typer.Exit(1)

    if not formation.loaded_agents:
        ch.error(f"Formation '{formation.id}' has no loaded agents.")
        raise typer.Exit(1)

    if not plain and not json_output:
        ch.info(
            f"Running council deliberation with {len(formation.loaded_agents)} agents "
            f"({rounds} round{'s' if rounds > 1 else ''})..."
        )

    result = run_council(
        formation=formation,
        question=question,
        rounds=rounds,
        timeout_per_agent=timeout,
    )

    if json_output:
        print(json_module.dumps(result, indent=2))
        return

    if plain:
        print(f"formation={result.get('pack', '')}")
        print(f"confidence={result.get('overall_confidence', 0)}")
        print(f"duration_ms={result.get('total_duration_ms', 0)}")
        print(f"agents={result.get('agents_count', 0)}")
        print("---")
        print(result.get("final_decision", ""))
        return

    # Rich formatted output
    ch.console.print()
    ch.console.print(
        f"[bold cyan]Council Decision[/bold cyan] [dim]({result.get('formation', '')})[/dim]"
    )
    ch.console.print(f"[dim]Question: {question}[/dim]")
    ch.console.print()

    # Show round summaries
    for rnd in result.get("rounds", []):
        ch.console.print(f"[bold]Round {rnd['round']}:[/bold]")
        for resp in rnd.get("responses", []):
            confidence = resp.get("confidence", 0)
            conf_bar = "|" * int(confidence * 10)
            status = "[green]" if confidence > 0.7 else "[yellow]" if confidence > 0.4 else "[red]"
            ch.console.print(
                f"  {status}{resp['name']}[/] ({resp['role']}): "
                f"confidence {confidence:.2f} {conf_bar} "
                f"[dim]{resp.get('duration_ms', 0)}ms[/dim]"
            )
        ch.console.print()

    # Final decision
    ch.console.print("[bold green]Final Decision:[/bold green]")
    ch.console.print(result.get("final_decision", "[No decision]"))
    ch.console.print()
    ch.console.print(
        f"[dim]Overall confidence: {result.get('overall_confidence', 0):.2f} | "
        f"Duration: {result.get('total_duration_ms', 0)}ms[/dim]"
    )
