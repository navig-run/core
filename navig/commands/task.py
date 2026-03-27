"""
navig task — Natural-language task router

Routes free-form instructions through available providers (email, calendar,
comms, etc.) and renders a timeline of results.

Usage:
    navig task "Send a daily digest to the team"
    navig task "Schedule a review for tomorrow" --dry-run
    navig task "Deploy staging" --json
"""

from __future__ import annotations

import typer

task_app = typer.Typer(
    name="task",
    help="Route natural-language instructions through available providers.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@task_app.callback()
def task_callback(ctx: typer.Context) -> None:
    """task — run without subcommand to pass an instruction directly."""
    if ctx.invoked_subcommand is None and not ctx.args:
        from navig.cli import show_subcommand_help

        show_subcommand_help("task", ctx)
        raise typer.Exit()


@task_app.command("run", hidden=True)
@task_app.command()
def task_run(
    ctx: typer.Context,
    instruction: str = typer.Argument(..., help="Natural-language instruction to route"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview routing without executing"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Route a natural-language instruction through available task providers.

    Examples:
        navig task "Send Slack message to team"
        navig task "Check server health on production"
        navig task "Summarise last 10 commits" --json
    """
    import time

    from navig.providers.task_bridge import TaskBridge, build_default_providers
    from navig.ui import renderer

    providers = build_default_providers()
    bridge = TaskBridge(providers)

    if dry_run:
        # Just show which providers would handle this
        scores = [(p.name, p.can_handle(instruction)) for p in providers]
        scores.sort(key=lambda x: x[1], reverse=True)
        rows = [
            {
                "Provider": name,
                "Score": f"{score:.2f}",
                "Would handle": "yes" if score > 0.1 else "no",
            }
            for name, score in scores
        ]
        renderer.render_fleet_table(
            rows,
            title=f"[dry-run] Routing: {instruction[:50]}",
            columns=["Provider", "Score", "Would handle"],
        )
        return

    ts = time.strftime("%H:%M:%S")
    results = bridge.route(instruction)

    if json_out:
        import json

        print(
            json.dumps(
                [
                    {
                        "provider": r.provider,
                        "success": r.success,
                        "output": r.output,
                        "error": r.error,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return

    # Render as event timeline
    events = []
    for r in results:
        from navig.ui.models import Event as TimelineEvent

        events.append(
            TimelineEvent(
                timestamp=ts,
                icon="✓" if r.success else "✗",
                label=r.provider,
                detail=r.output[:80] if r.success else (r.error or "failed"),
                color="green" if r.success else "red",
            )
        )

    if events:
        from navig.ui.timeline import render_event_timeline

        render_event_timeline(events, title=f"Task: {instruction[:50]}")
    else:
        from navig import console_helper as ch

        ch.warning("No providers available to handle this instruction.")
        ch.dim("  Install gateway channels or configure providers.")
