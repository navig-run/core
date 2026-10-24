"""CLI commands for ``navig mind`` — NAVIG MIND cognitive/language preferences.

  navig mind status                      — show the resolved MIND profile
  navig mind on | off                    — enable/disable the whole MIND layer
  navig mind lexicon <density>           — vocabulary-learning density
  navig mind digitalghost <mode>         — DIGITALGHOST commentary on/off/frequency
"""

from __future__ import annotations

import typer

from navig.console_helper import get_console

mind_app = typer.Typer(
    help="NAVIG MIND — personal cognitive/language preferences",
    invoke_without_command=True,
    no_args_is_help=False,
)

_LEXICON_DENSITIES = {"off", "minimal", "balanced", "detailed", "intensive"}
_DIGITALGHOST_MODES = {"on", "off", "rare", "balanced", "deep", "retrospective"}


@mind_app.callback()
def mind_callback(ctx: typer.Context) -> None:
    """Show the profile when run without a subcommand."""
    if ctx.invoked_subcommand is None:
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("mind", mind_app)


# ── navig mind status ─────────────────────────────────────────────────────


@mind_app.command("status")
def mind_status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show the effective MIND profile (preference hierarchy + firewall)."""
    from navig.mind import get_effective_mind  # noqa: PLC0415

    profile = get_effective_mind().to_dict()
    if json_output:
        import json as _json

        typer.echo(_json.dumps(profile, indent=2))
        return
    _render_status(profile)


def _render_status(p: dict) -> None:
    from rich.table import Table  # noqa: PLC0415

    console = get_console()
    state = "[green]enabled[/green]" if p["enabled"] else "[red]disabled[/red]"
    firewall = "[green]enforced[/green]" if p["artifact_language_firewall"] else "[red]OFF[/red]"
    lex = p["lexicon"]
    ghost = p["digitalghost"]
    table = Table(title="🧠 NAVIG MIND", border_style="dim", show_header=False)
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="bold")
    table.add_row("Status", state)
    table.add_row("Conversation", p["conversation_language"])
    table.add_row("Creative", p["creative_language"])
    table.add_row("Engineering", p["engineering_language"])
    table.add_row("Commentary", p["commentary_language"])
    table.add_row(
        "Lexicon",
        f"{'on' if lex['enabled'] else 'off'} · {lex['density']}",
    )
    table.add_row(
        "DIGITALGHOST",
        f"{'on' if ghost['enabled'] else 'off'} · {ghost['frequency']} · {', '.join(ghost['roles'])}",
    )
    table.add_row("Artifact firewall", firewall)
    console.print(table)
    console.print(
        "\n[dim]Hierarchy: session > project > global > default. "
        "The artifact-language firewall is non-negotiable.[/dim]"
    )


# ── navig mind on / off ───────────────────────────────────────────────────


@mind_app.command("on")
def mind_on() -> None:
    """Enable the MIND layer."""
    _set_enabled(True)


@mind_app.command("off")
def mind_off() -> None:
    """Disable the MIND layer (the agent reverts to its default prompt)."""
    _set_enabled(False)


def _set_enabled(enabled: bool) -> None:
    from navig.config import get_config_manager  # noqa: PLC0415

    get_config_manager().set_global("mind.enabled", enabled)
    word = "enabled" if enabled else "disabled"
    get_console().print(f"[green]✓[/green] NAVIG MIND {word}.")


# ── navig mind lexicon <density> ──────────────────────────────────────────


@mind_app.command("lexicon")
def mind_lexicon(
    density: str = typer.Argument(..., help="off | minimal | balanced | detailed | intensive"),
) -> None:
    """Set vocabulary-learning density (only in explanatory prose)."""
    from navig.config import get_config_manager  # noqa: PLC0415

    d = (density or "").strip().lower()
    if d not in _LEXICON_DENSITIES:
        raise typer.BadParameter(f"density must be one of {sorted(_LEXICON_DENSITIES)}")
    cm = get_config_manager()
    cm.set_global("mind.lexicon.density", d)
    if d == "off":
        cm.set_global("mind.lexicon.enabled", False)
    else:
        cm.set_global("mind.lexicon.enabled", True)
    get_console().print(f"[green]✓[/green] Lexicon density: [bold]{d}[/bold].")


# ── navig mind digitalghost <mode> ────────────────────────────────────────


@mind_app.command("digitalghost")
def mind_digitalghost(
    mode: str = typer.Argument(..., help="on | off | rare | balanced | deep | retrospective"),
) -> None:
    """Set DIGITALGHOST commentary on/off/frequency."""
    from navig.config import get_config_manager  # noqa: PLC0415

    m = (mode or "").strip().lower()
    if m not in _DIGITALGHOST_MODES:
        raise typer.BadParameter(f"mode must be one of {sorted(_DIGITALGHOST_MODES)}")
    cm = get_config_manager()
    if m == "off":
        cm.set_global("mind.digitalghost.enabled", False)
    else:
        cm.set_global("mind.digitalghost.enabled", True)
        if m != "on":
            cm.set_global("mind.digitalghost.frequency", m)
    get_console().print(f"[green]✓[/green] DIGITALGHOST: [bold]{m}[/bold].")
