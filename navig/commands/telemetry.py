"""navig telemetry — the one anonymous install ping, and how to turn it off.

This said "opt-in", which was wrong in a way that mattered: the only telemetry NAVIG
sends (``navig.onboarding.telemetry``) is opt-OUT — it fires once on first install
unless you decline. Describing an opt-out system as opt-in tells the reader that doing
nothing means sending nothing.

Both switches are honoured, and ``navig telemetry`` reports whichever applies:
  * ``NAVIG_NO_TELEMETRY=1`` — hard env opt-out, wins over everything;
  * ``navig telemetry disable`` — writes ``telemetry.enabled=false``.
"""
import typer

from navig.console_helper import get_console

telemetry_app = typer.Typer(help="Manage NAVIG telemetry / analytics opt-in", no_args_is_help=False)
console = get_console()


@telemetry_app.callback(invoke_without_command=True)
def telemetry_default(ctx: typer.Context):
    """Show telemetry status."""
    if ctx.invoked_subcommand:
        return
    try:
        import os

        from navig.onboarding.telemetry import (
            _OPT_OUT_VAR,
            telemetry_opted_out_in_config,
        )

        # Report the state that ACTUALLY applies. This used to read
        # `telemetry.enabled` with default=False and call that the answer — but nothing
        # consumed that key, and the real gate is the env var, whose default is ON.
        # So a fresh install was told "disabled" while the install ping would fire.
        if os.environ.get(_OPT_OUT_VAR):
            console.print(f"Telemetry: [dim]disabled[/dim] (via {_OPT_OUT_VAR})")
        elif telemetry_opted_out_in_config(strict=True):
            console.print("Telemetry: [dim]disabled[/dim] (via navig telemetry disable)")
        else:
            console.print("Telemetry: [green]enabled[/green] — one anonymous install ping")
            console.print("[dim]Turn it off with: navig telemetry disable[/dim]")
    except Exception:
        console.print("[dim]Telemetry: unknown[/dim]")


@telemetry_app.command("enable")
def telemetry_enable():
    """Enable telemetry."""
    try:
        from navig.config import ConfigManager

        ConfigManager().set("telemetry.enabled", True)
        console.print("[green]Telemetry enabled.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")


@telemetry_app.command("disable")
def telemetry_disable():
    """Disable telemetry."""
    try:
        from navig.config import ConfigManager

        ConfigManager().set("telemetry.enabled", False)
        console.print("[green]Telemetry disabled.[/green]")
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
