"""
navig cost — LLM session cost reporting.

Commands:
  navig cost            Show current session cost summary.
  navig cost history    Show recent session cost history.
  navig cost clear      Clear session cost history.
"""

from __future__ import annotations

from typing import Annotated

import typer

from navig import console_helper as ch

cost_app = typer.Typer(
    name="cost",
    help="Show LLM token usage and USD cost for the current and past sessions.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@cost_app.callback(invoke_without_command=True)
def cost_default(ctx: typer.Context) -> None:
    """Show current session cost summary (default sub-command)."""
    if ctx.invoked_subcommand is not None:
        return
    _show_current()


def _show_current() -> None:
    from navig.cost_tracker import get_session_tracker

    tracker = get_session_tracker()
    inp, out, crd = tracker.total_tokens()

    if inp == 0 and out == 0:
        ch.dim("No LLM calls recorded in this session.")
        return

    summary = tracker.format_summary()
    try:
        ch.console.print(summary)
    except Exception:  # noqa: BLE001
        typer.echo(summary)


@cost_app.command("history")
def cost_history(
    last: Annotated[
        int,
        typer.Option("--last", "-n", help="Number of past sessions to show."),
    ] = 10,
) -> None:
    """Show recent session cost history."""
    from navig.cost_tracker import SessionCostTracker

    sessions = SessionCostTracker.load_history(last_n=last)
    if not sessions:
        ch.dim("No session cost history found.")
        return

    try:
        table = ch.Table(title=f"Session Cost History (last {len(sessions)})")
        table.add_column("Session ID", style="cyan", no_wrap=True)
        table.add_column("Started", style="dim")
        table.add_column("In tokens", justify="right")
        table.add_column("Out tokens", justify="right")
        table.add_column("Cost (USD)", justify="right", style="green")

        for s in sessions:
            table.add_row(
                s.session_id,
                s.started_at[:19].replace("T", " "),
                f"{s.total_input_tokens:,}",
                f"{s.total_output_tokens:,}",
                f"${s.total_cost_usd:.6f}",
            )

        ch.console.print(table)
    except Exception:  # noqa: BLE001
        # Plain-text fallback (Rich unavailable)
        for s in sessions:
            typer.echo(
                f"{s.session_id}  {s.started_at[:19]}  "
                f"in={s.total_input_tokens}  out={s.total_output_tokens}  "
                f"${s.total_cost_usd:.6f}"
            )


@cost_app.command("clear")
def cost_clear(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
) -> None:
    """Clear session cost history (irreversible)."""
    from navig.cost_tracker import SessionCostTracker

    auto_yes = yes or (ctx.obj or {}).get("yes", False)
    if not auto_yes:
        confirmed = typer.confirm("Clear all session cost history?", default=False)
        if not confirmed:
            ch.dim("Aborted.")
            raise typer.Exit(0)

    history_path = SessionCostTracker._history_path_static()
    if not history_path.exists():
        ch.dim("No history file found.")
        return

    try:
        history_path.unlink()
        ch.success(f"Session cost history cleared ({history_path})")
    except OSError as exc:
        ch.error(f"Failed to clear history: {exc}")
        raise typer.Exit(1) from exc
